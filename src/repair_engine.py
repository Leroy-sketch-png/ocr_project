import copy
import itertools
import logging
from typing import Any, Dict, List, Optional

from .cell_ocr import targeted_ocr
from .models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_INFERRED,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    FieldValue,
    Token,
)
from .value_parser import parse_numeric

logger = logging.getLogger(__name__)

_RESIDUAL_TOLERANCE = 1e-2

# Accounting equations used for repair and inverse-search.
# All summands are additive; Cost of Sales carries a negative value so
# Revenue + CoS = Gross Profit works directly.
EQUATIONS = [
    ("Gross Profit/Loss",        ["Revenue", "Cost of Sales"]),
    ("Current Assets",           ["Cash and Cash Equivalents", "Trade Receivables"]),
    ("Total Assets",             ["Current Assets", "Non-Current Assets"]),
    ("Total Liabilities",        ["Current Liabilities", "Non-Current Liabilities"]),
    ("Total Equity",             ["Paid Up Capital", "Retained Earnings"]),
    ("Total Assets",             ["Total Liabilities", "Total Equity"]),
    # PBT = Net Profit + Income Tax Expense
    # Anchors digit-mutation repair when OCR misreads a digit in PBT.
    ("Profit/Loss Before Tax",   ["Net Profit/Loss", "Income Tax Expense"]),
]

# Common single-character OCR confusions.
# Each key maps to the characters it is frequently misread as.
CONFUSION_SET: Dict[str, List[str]] = {
    "0": ["8", "6", "9"],
    "1": ["7", "4"],   # '1' and '4' are confused in many serif/small fonts
    "2": ["Z", "7"],
    "3": ["8"],
    "4": ["A", "1"],   # bidirectional with '1'
    "5": ["S", "6", "8"],
    "6": ["5", "8", "0"],
    "7": ["1"],
    "8": ["0", "3", "6", "9", "S"],
    "9": ["0", "8"],
}

# A digit-mutation repair is safe if the value change is within this ratio.
# This guard is intentionally bypassed for equation-anchored repairs because
# a zero residual is already mathematical proof the new value is correct.
_MAX_REPAIR_DELTA_RATIO = 0.15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_equation_residual(
    fields: Dict[str, FieldValue], target: str, summands: List[str]
) -> Optional[float]:
    """Return |target - sum(summands)|, or None if any field is missing."""
    if target not in fields or fields[target].value is None:
        return None
    total = 0.0
    for s in summands:
        if s not in fields or fields[s].value is None:
            return None
        total += fields[s].value
    return abs(fields[target].value - total)


def _residual_ok(residual: Optional[float]) -> bool:
    return residual is not None and abs(residual) < _RESIDUAL_TOLERANCE


def _is_safe_repair(
    current_val: Optional[float], proposed_val: float, repair_type: str
) -> bool:
    """Return True if the proposed repair is within acceptable bounds.

    sign_flip and column_selection repairs are always accepted because they
    are structurally validated (wrong sign or wrong column, not digit noise).
    Equation-anchored repairs (residual == 0 proven) also bypass the ratio
    guard — mathematical proof supersedes the heuristic.
    """
    if repair_type in ("sign_flip", "column_selection", "equation_anchored"):
        return True
    if current_val is None or current_val == 0.0:
        return True
    return abs(proposed_val - current_val) / abs(current_val) <= _MAX_REPAIR_DELTA_RATIO


def generate_candidates(raw_text: str) -> List[str]:
    """Generate OCR-error candidates by single-character substitution."""
    if not raw_text:
        return []
    clean = raw_text.replace(" ", "")
    candidates = []
    for i, ch in enumerate(clean):
        for alt in CONFUSION_SET.get(ch, []):
            candidates.append(clean[:i] + alt + clean[i + 1:])
    if clean.endswith("0"):
        candidates.append(clean[:-1])
    candidates.append(clean + "0")
    if "(" in clean and ")" in clean:
        candidates.append(clean.replace("(", "").replace(")", ""))
    elif "-" in clean:
        candidates.append(clean.replace("-", ""))
    else:
        candidates.append("-" + clean)
    return list(set(candidates))


def _make_field_value(
    name: str,
    val: float,
    raw_text: str,
    token: Optional[Token],
    reason: str,
    confidence: str,
) -> FieldValue:
    return FieldValue(
        name=name,
        value=val,
        raw_text=raw_text,
        page=token.page if token else None,
        tokens=[token] if token else [],
        bbox=token.bbox if token else None,
        valid=True,
        reason=reason,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Pre-phase rules
# ---------------------------------------------------------------------------

def _apply_null_cos_without_gp(fields: Dict[str, FieldValue]) -> None:
    """Null Cost of Sales when no Gross Profit line exists.

    Cost of Sales is only meaningful in a document that has a gross profit
    concept. If GP is absent, a CoS match is almost certainly a false positive
    from an operating-expense row (e.g. 'Purchase of services' in a
    services-only P&L). Nulling it prevents downstream equation corruption.
    """
    gp = fields.get("Gross Profit/Loss")
    cos = fields.get("Cost of Sales")
    if (gp is None or gp.value is None) and (cos is not None and cos.value is not None):
        logger.debug("[NULL-CoS] GP absent — nulling Cost of Sales (was %s)", cos.value)
        cos.value = None
        cos.reason = "nulled_no_gross_profit"
        cos.confidence = CONFIDENCE_LOW


def _apply_zero_ncl_inference(fields: Dict[str, FieldValue]) -> None:
    """Infer Current Liabilities = Total Liabilities when NCL is truly absent.

    Handles documents (e.g. early-stage companies) where there are no
    non-current liabilities at all — the single liabilities line IS the total.
    Fires unconditionally. The function itself is a no-op if NCL already has a value.
    """
    tl = fields.get("Total Liabilities")
    ncl = fields.get("Non-Current Liabilities")
    cl = fields.get("Current Liabilities")
    if (
        tl is not None and tl.value is not None
        and (ncl is None or ncl.value is None)
        and (cl is None or cl.value is None)
    ):
        logger.debug(
            "[ZERO-NCL] Inferring Current Liabilities = Total Liabilities = %s",
            tl.value,
        )
        fields["Current Liabilities"] = FieldValue(
            name="Current Liabilities",
            value=tl.value,
            raw_text=tl.raw_text,
            page=tl.page,
            tokens=tl.tokens,
            bbox=tl.bbox,
            valid=True,
            reason="zero_ncl_inference",
            confidence=CONFIDENCE_LOW,
        )


# ---------------------------------------------------------------------------
# Main repair loop
# ---------------------------------------------------------------------------

def apply_math_repairs(
    fields: Dict[str, FieldValue],
    processed_images: Dict[int, Any],
    all_tokens: List[Token] = None,
    optimization_mode: bool = False,
) -> Dict[str, FieldValue]:
    """Apply three-phase math repair to extracted fields.

    Phase 0A — Null-CoS-without-GP: prevent false CoS when no GP line exists.
    Phase 0B — Zero-NCL inference: CL = TL when NCL is structurally absent.
               Fires unconditionally. The function itself is a no-op if NCL already has a value.
    Phase 1  — Digit mutation: flip one OCR-confused character per field.
    Phase 1.5 — Column selection: try alternate column values from the row.
    Phase 2  — Sniper OCR: re-OCR the suspicious bounding box.
    Phase 3  — Inverse search: if exactly one field is missing in an equation,
               scan all tokens for the value that closes the equation.
               (optimization_mode only)
    """
    repaired = copy.deepcopy(fields)

    if logger.isEnabledFor(logging.DEBUG):
        for k, v in fields.items():
            if v.value is not None:
                logger.debug("[REPAIR-IN] %s = %s (conf=%s)", k, v.value,
                             getattr(v, 'confidence', '?'))

    # Phase 0A
    _apply_null_cos_without_gp(repaired)

    # Phase 0B — zero-NCL inference
    _apply_zero_ncl_inference(repaired)

    for target, summands in EQUATIONS:
        suspects = [target] + summands
        residual = compute_equation_residual(repaired, target, summands)
        logger.debug("[EQ] %s = %s  residual=%s", target, summands, residual)

        # Phase 3: inverse search (optimization_mode only)
        if optimization_mode:
            missing = [
                s for s in suspects
                if s not in repaired or repaired[s].value is None
            ]
            if len(missing) == 1 and all_tokens is not None:
                m = missing[0]
                others = [s for s in suspects if s != m]
                if all(s in repaired and repaired[s].value is not None for s in others):
                    cand_lists = []
                    for s in others:
                        fv = repaired[s]
                        cands = [(fv.raw_text, fv.value)]
                        if fv.row_candidates:
                            cands += [(r, v) for r, v in fv.row_candidates if v != fv.value]
                        cand_lists.append(cands)

                    for combo in itertools.product(*cand_lists):
                        assign = {s: v for s, (_, v) in zip(others, combo)}
                        if m == target:
                            expected = sum(assign[s] for s in summands)
                        else:
                            expected = assign[target] - sum(
                                assign[s] for s in summands if s != m
                            )
                        if abs(expected) < _RESIDUAL_TOLERANCE:
                            continue

                        for token in all_tokens:
                            cv = parse_numeric(token.text)
                            if cv is not None and abs(cv - expected) < 0.5:
                                # Update any combinatorial-swapped fields
                                for s, (raw, val) in zip(others, combo):
                                    if repaired[s].value != val:
                                        repaired[s].value = val
                                        repaired[s].raw_text = raw
                                        repaired[s].tokens = []
                                        repaired[s].reason = "inverse_search_combinatorial"
                                        repaired[s].confidence = CONFIDENCE_MEDIUM
                                        logger.debug("  [INV-COMBO] %s -> %s", s, val)
                                # Fill the missing field
                                if m in repaired:
                                    repaired[m].value = cv
                                    repaired[m].raw_text = token.text
                                    repaired[m].page = token.page
                                    repaired[m].bbox = token.bbox
                                    repaired[m].reason = "inverse_search"
                                    repaired[m].confidence = CONFIDENCE_MEDIUM
                                    repaired[m].tokens = [token]
                                else:
                                    repaired[m] = _make_field_value(
                                        m, cv, token.text, token,
                                        "inverse_search", CONFIDENCE_MEDIUM,
                                    )
                                logger.debug("  [INV] %s = %s", m, cv)
                                break
                        else:
                            # Phase 3 continuation: split-token merge pass
                            # Handles values OCR split into adjacent tokens (e.g. "1180" + "159" → 1180159)
                            if m not in repaired or repaired[m].value is None:
                                # Build a sorted list of tokens for merge scanning
                                sorted_tokens = sorted(all_tokens, key=lambda t: (t.page, t.bbox[1], t.bbox[0]))
                                for i in range(len(sorted_tokens) - 1):
                                    ta = sorted_tokens[i]
                                    tb = sorted_tokens[i + 1]
                                    # Must be same page, vertically aligned, small horizontal gap
                                    if ta.page != tb.page:
                                        continue
                                    y_overlap = abs(ta.bbox[1] - tb.bbox[1])
                                    x_gap = tb.bbox[0] - ta.bbox[2]
                                    if y_overlap > 15 or x_gap < 0 or x_gap > 60:
                                        continue
                                    merged_text = ta.text + tb.text
                                    cv = parse_numeric(merged_text)
                                    if cv is None:
                                        continue
                                    if abs(cv - expected) < 0.5:
                                        merged_token = Token(
                                            text=merged_text,
                                            bbox=(ta.bbox[0], ta.bbox[1], tb.bbox[2], tb.bbox[3]),
                                            page=ta.page,
                                            confidence=min(ta.confidence, tb.confidence),
                                        )
                                        if m in repaired:
                                            repaired[m].value = cv
                                            repaired[m].raw_text = merged_text
                                            repaired[m].page = merged_token.page
                                            repaired[m].bbox = merged_token.bbox
                                            repaired[m].reason = "inverse_search_merged"
                                            repaired[m].confidence = CONFIDENCE_MEDIUM
                                            repaired[m].tokens = [merged_token]
                                        else:
                                            repaired[m] = _make_field_value(
                                                m, cv, merged_text, merged_token,
                                                "inverse_search_merged", CONFIDENCE_MEDIUM,
                                            )
                                        logger.debug("  [INV-MERGE] %s = %s (from '%s'+'%s')", m, cv, ta.text, tb.text)
                                        break
                                else:
                                    continue
                        break

        best_repair = None

        # Phase 1.6: joint mutation with missing field search
        # Handles the catch-22 where one field is wrong (misread digit) and another is missing (None),
        # so neither can be corrected alone. We speculatively mutate the wrong field, compute the expected
        # missing value to balance the equation, and scan all_tokens for a match.
        if not best_repair and optimization_mode and all_tokens is not None:
            missing_fields = [s for s in suspects if s not in repaired or repaired[s].value is None]
            if len(missing_fields) == 1:
                m = missing_fields[0]
                others = [s for s in suspects if s != m]
                if all(s in repaired and repaired[s].value is not None for s in others):
                    # If the equation already balances with the missing field as 0, do not mutate/search
                    if m == target:
                        val_others = sum(repaired[s].value for s in summands if s != m)
                        is_balanced = abs(val_others) < _RESIDUAL_TOLERANCE
                    else:
                        val_others = sum(repaired[s].value for s in summands if s != m)
                        is_balanced = abs(repaired[target].value - val_others) < _RESIDUAL_TOLERANCE
                    if not is_balanced:
                        for suspect_a in others:
                            raw_a = repaired[suspect_a].raw_text
                            if not raw_a:
                                continue
                            old_a = repaired[suspect_a].value
                            for cand_a in generate_candidates(raw_a):
                                cv_a = parse_numeric(cand_a)
                                if cv_a is None or cv_a == old_a:
                                    continue
                                repaired[suspect_a].value = cv_a
                                if m == target:
                                    expected = sum(repaired[s].value for s in summands)
                                else:
                                    expected = repaired[target].value - sum(
                                        repaired[s].value for s in summands if s != m
                                    )
                                if abs(expected) < _RESIDUAL_TOLERANCE:
                                    continue
                                if cv_a != old_a and abs(expected) < 100.0:
                                    continue
                                    
                                # Search for expected value
                                matched_token = None
                                matched_val = None
                                for token in all_tokens:
                                    cv = parse_numeric(token.text)
                                    if cv is not None and abs(cv - expected) < 0.5:
                                        matched_token = token
                                        matched_val = cv
                                        break
                                        
                                if not matched_token:
                                    sorted_tokens = sorted(all_tokens, key=lambda t: (t.page, t.bbox[1], t.bbox[0]))
                                    for idx in range(len(sorted_tokens) - 1):
                                        ta = sorted_tokens[idx]
                                        tb = sorted_tokens[idx + 1]
                                        if ta.page != tb.page:
                                            continue
                                        y_overlap = abs(ta.bbox[1] - tb.bbox[1])
                                        x_gap = tb.bbox[0] - ta.bbox[2]
                                        if y_overlap > 15 or x_gap < 0 or x_gap > 60:
                                            continue
                                        merged_text = ta.text + tb.text
                                        cv = parse_numeric(merged_text)
                                        if cv is not None and abs(cv - expected) < 0.5:
                                            matched_val = cv
                                            matched_token = Token(
                                                text=merged_text,
                                                bbox=(ta.bbox[0], ta.bbox[1], tb.bbox[2], tb.bbox[3]),
                                                page=ta.page,
                                                confidence=min(ta.confidence, tb.confidence)
                                            )
                                            break
                                            
                                if matched_token and matched_val is not None:
                                    # Fill the missing field on the spot
                                    if m in repaired:
                                        repaired[m].value = matched_val
                                        repaired[m].raw_text = matched_token.text
                                        repaired[m].page = matched_token.page
                                        repaired[m].bbox = matched_token.bbox
                                        repaired[m].reason = "inverse_search_merged" if len(matched_token.text) > len(ta.text) else "inverse_search"
                                        repaired[m].confidence = CONFIDENCE_MEDIUM
                                        repaired[m].tokens = [matched_token]
                                    else:
                                        repaired[m] = _make_field_value(
                                            m, matched_val, matched_token.text, matched_token,
                                            "inverse_search", CONFIDENCE_MEDIUM,
                                        )
                                    logger.debug("  [INV-JOINT] filled missing field %s = %s", m, matched_val)
                                    best_repair = (suspect_a, cand_a, cv_a, "equation_anchored")
                                    break
                            repaired[suspect_a].value = old_a
                            if best_repair:
                                break

        # Recompute after inverse search
        residual = compute_equation_residual(repaired, target, summands)
        if (residual is None or _residual_ok(residual)) and not best_repair:
            continue

        # Phase 1: digit mutation
        for suspect in suspects:
            if suspect not in repaired or repaired[suspect].value is None:
                continue
            raw = repaired[suspect].raw_text
            if not raw:
                continue
            for cand in generate_candidates(raw):
                cv = parse_numeric(cand)
                if cv is None:
                    continue
                old = repaired[suspect].value
                repaired[suspect].value = cv
                if _residual_ok(compute_equation_residual(repaired, target, summands)):
                    best_repair = (suspect, cand, cv, "equation_anchored")
                repaired[suspect].value = old
                if best_repair:
                    break
            if best_repair:
                break

        # Phase 1.5: column selection
        if not best_repair:
            for suspect in suspects:
                if suspect not in repaired or repaired[suspect].value is None:
                    continue
                fv = repaired[suspect]
                for cand_raw, cv in (fv.row_candidates or []):
                    if cv == fv.value:
                        continue
                    old = fv.value
                    fv.value = cv
                    if _residual_ok(compute_equation_residual(repaired, target, summands)):
                        best_repair = (suspect, cand_raw, cv, "column_selection")
                        logger.debug("  [COL] %s -> %s", suspect, cv)
                    fv.value = old
                    if best_repair:
                        break
                if best_repair:
                    break


        # Phase 2: sniper OCR
        if not best_repair:
            for suspect in suspects:
                if suspect not in repaired or repaired[suspect].value is None:
                    continue
                fv = repaired[suspect]
                if fv.page in processed_images and fv.bbox is not None:
                    new_text = targeted_ocr(processed_images[fv.page], fv.bbox)
                    cv = parse_numeric(new_text)
                    if cv is not None and cv != fv.value:
                        old = fv.value
                        fv.value = cv
                        if _residual_ok(compute_equation_residual(repaired, target, summands)):
                            rtype = (
                                "sign_flip"
                                if old is not None and cv is not None and old * cv < 0
                                else "equation_anchored"
                            )
                            best_repair = (suspect, new_text, cv, rtype)
                        fv.value = old
                        if best_repair:
                            break

        if best_repair:
            field, cand_raw, cand_val, repair_type = best_repair
            current_val = repaired[field].value
            if _is_safe_repair(current_val, cand_val, repair_type):
                repaired[field].raw_text = cand_raw
                repaired[field].value = cand_val
                repaired[field].reason = "accounting_repair"
                repaired[field].confidence = CONFIDENCE_MEDIUM
                logger.debug(
                    "[REPAIR] %s: %s -> %s (type=%s)",
                    field,
                    fields[field].raw_text if field in fields else None,
                    cand_raw,
                    repair_type,
                )
            else:
                logger.debug(
                    "[REPAIR-REJECTED] %s: %s -> %s exceeds delta ratio",
                    field, current_val, cand_val,
                )

    return repaired
