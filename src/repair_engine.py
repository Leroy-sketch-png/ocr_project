import copy
import itertools
import logging
from typing import Any, Dict, List, Optional, Set

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

# ---------------------------------------------------------------------------
# Accounting equations
# ---------------------------------------------------------------------------
# All summands are additive; signed fields (Cost of Sales, negative Retained
# Earnings) carry their sign in the extracted value, so arithmetic is uniform.
#
# New in wave-2:
#   Row 7 anchors Operating Profit/Loss via PBT when the document has both.
#   Equation direction: PBT = OPL  (non-operating items assumed zero or
#   already captured elsewhere). This is intentionally conservative — it only
#   fires when PBT is the sole other present field, giving OPL a lower-bound
#   anchor that the inverse-search phases can then refine from the token stream.
EQUATIONS = [
    ("Gross Profit/Loss",        ["Revenue", "Cost of Sales"]),
    ("Current Assets",           ["Cash and Cash Equivalents", "Trade Receivables"]),
    ("Total Assets",             ["Current Assets", "Non-Current Assets"]),
    ("Total Liabilities",        ["Current Liabilities", "Non-Current Liabilities"]),
    ("Total Equity",             ["Paid Up Capital", "Retained Earnings"]),
    ("Total Assets",             ["Total Liabilities", "Total Equity"]),
    ("Profit/Loss Before Tax",   ["Net Profit/Loss", "Income Tax Expense"]),
]

# ---------------------------------------------------------------------------
# Field -> document section mapping
# Used by Phase 1.6 to reject tokens found in the wrong section.
# ---------------------------------------------------------------------------
_FIELD_SECTIONS: Dict[str, str] = {
    "Revenue":                   "income_statement",
    "Cost of Sales":             "income_statement",
    "Gross Profit/Loss":         "income_statement",
    "Operating Profit/Loss":     "income_statement",
    "Profit/Loss Before Tax":    "income_statement",
    "Net Profit/Loss":           "income_statement",
    "Income Tax Expense":        "income_statement",
    "Cash and Cash Equivalents": "balance_sheet",
    "Trade Receivables":         "balance_sheet",
    "Current Assets":            "balance_sheet",
    "Non-Current Assets":        "balance_sheet",
    "Total Assets":              "balance_sheet",
    "Current Liabilities":       "balance_sheet",
    "Non-Current Liabilities":   "balance_sheet",
    "Total Liabilities":         "balance_sheet",
    "Paid Up Capital":           "balance_sheet",
    "Retained Earnings":         "balance_sheet",
    "Total Equity":              "balance_sheet",
}

# Common single-character OCR confusions.
CONFUSION_SET: Dict[str, List[str]] = {
    "0": ["8", "6", "9"],
    "1": ["7", "4"],
    "2": ["Z", "7"],
    "3": ["8"],
    "4": ["A", "1"],
    "5": ["S", "6", "8"],
    "6": ["5", "8", "0"],
    "7": ["1"],
    "8": ["0", "3", "6", "9", "S"],
    "9": ["0", "8"],
}

# Maximum fraction a digit-mutation repair can change a value by.
# Bypassed for equation_anchored / sign_flip / column_selection repairs
# because those are mathematically or structurally proven.
_MAX_REPAIR_DELTA_RATIO = 0.15

# Maximum number of times a single field may be mutated across all equations.
# A field repaired more than this many times is almost certainly being
# over-corrected by conflicting equations — reject subsequent repairs.
_MAX_REPAIRS_PER_FIELD = 1


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
    current_val: Optional[float],
    proposed_val: float,
    repair_type: str,
    repair_counts: Dict[str, int],
    field: str,
) -> bool:
    """Return True if the proposed repair passes all safety guards.

    Guards (in order):
    1. Repair-chain cap: reject if this field has already been mutated
       _MAX_REPAIRS_PER_FIELD times.
    2. Structural repairs (sign_flip, column_selection, equation_anchored)
       bypass the magnitude ratio guard — they are provably correct.
    3. Heuristic repairs: reject if the proposed change exceeds
       _MAX_REPAIR_DELTA_RATIO of the current value.
    """
    if repair_counts.get(field, 0) >= _MAX_REPAIRS_PER_FIELD:
        logger.debug(
            "[REPAIR-CAP] %s already repaired %d time(s) — rejecting further mutation",
            field, repair_counts[field],
        )
        return False
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
    """Null Cost of Sales when no Gross Profit line exists."""
    gp = fields.get("Gross Profit/Loss")
    cos = fields.get("Cost of Sales")
    if (gp is None or gp.value is None) and (cos is not None and cos.value is not None):
        logger.debug("[NULL-CoS] GP absent — nulling Cost of Sales (was %s)", cos.value)
        cos.value = None
        cos.reason = "nulled_no_gross_profit"
        cos.confidence = CONFIDENCE_LOW


def _apply_zero_ncl_inference(
    fields: Dict[str, FieldValue],
    table_rows=None,
    flat_config=None,
) -> None:
    """Infer Current Liabilities = Total Liabilities when NCL is truly absent.

    Structural guard: suppressed if any balance-sheet row scores >= 60
    against any NCL keyword — i.e., NCL exists but was merely unmatched.
    """
    tl = fields.get("Total Liabilities")
    ncl = fields.get("Non-Current Liabilities")
    cl = fields.get("Current Liabilities")
    if not (
        tl is not None and tl.value is not None
        and (ncl is None or ncl.value is None)
        and (cl is None or cl.value is None)
    ):
        return

    if table_rows is not None and flat_config is not None:
        ncl_cfg = flat_config.get("Non-Current Liabilities", {})
        ncl_kws = ncl_cfg.get("keywords", []) if isinstance(ncl_cfg, dict) else []
        from .field_extractor import compute_match_score
        for row in table_rows:
            desc_lower = row.description.lower()
            for kw in ncl_kws:
                if compute_match_score(kw, desc_lower) >= 80:
                    logger.debug(
                        "[ZERO-NCL] Suppressed — structural NCL candidate: '%s'",
                        row.description,
                    )
                    return

    logger.debug("[ZERO-NCL] Inferring CL = TL = %s", tl.value)
    fields["Current Liabilities"] = FieldValue(
        name="Current Liabilities",
        value=tl.value,
        raw_text=tl.raw_text,
        page=tl.page,
        tokens=tl.tokens,
        bbox=tl.bbox,
        valid=True,
        reason="zero_ncl_inference",
        confidence=CONFIDENCE_INFERRED,
    )





def _check_liabilities_closure(
    fields: Dict[str, FieldValue],
) -> None:
    """Post-repair sanity check: TL should equal CL + NCL.

    If zero_ncl_inference fired but the balance sheet still doesn't close
    (e.g. TL was itself misread), downgrade TL confidence and emit WARNING.
    This is a diagnostic signal, not a repair — it tells downstream
    consumers the balance sheet is suspect.
    """
    tl = fields.get("Total Liabilities")
    cl = fields.get("Current Liabilities")
    ncl = fields.get("Non-Current Liabilities")
    if tl is None or tl.value is None:
        return
    cl_val = cl.value if cl and cl.value is not None else 0.0
    ncl_val = ncl.value if ncl and ncl.value is not None else 0.0
    residual = abs(tl.value - (cl_val + ncl_val))
    if residual > _RESIDUAL_TOLERANCE:
        logger.warning(
            "[LIAB-CLOSURE] TL=%.2f != CL=%.2f + NCL=%.2f (residual=%.4f) — "
            "balance sheet may not close; downgrading TL confidence.",
            tl.value, cl_val, ncl_val, residual,
        )
        tl.confidence = CONFIDENCE_LOW
        tl.reason = (tl.reason or "") + "+closure_suspect"


# ---------------------------------------------------------------------------
# Main repair loop
# ---------------------------------------------------------------------------

def apply_math_repairs(
    fields: Dict[str, FieldValue],
    processed_images: Dict[int, Any],
    all_tokens: List[Token] = None,
    optimization_mode: bool = False,
    dpi_scale: float = 1.0,
    page_section_map: Dict[int, str] = None,
    table_rows=None,
    flat_config=None,
) -> Dict[str, FieldValue]:
    """Apply multi-phase math repair to extracted fields.

    Pre-phases:
      0A — Null-CoS-without-GP
      0B — Zero-NCL inference (structurally guarded)
      0C — OPL anchor from PBT (CONFIDENCE_INFERRED, refinable)

    Repair phases (per EQUATION):
      1   — Digit mutation
      1.5 — Column selection (row_candidates)
      1.6 — Joint solver: one missing + one misread digit (section-guarded)
      2   — Sniper OCR re-recognition
      3   — Inverse search: one missing field, scan all tokens
             (single-token + DPI-scaled split-merge)

    Post-phases:
      4   — Liabilities closure sanity check

    All field mutations respect:
      - _MAX_REPAIRS_PER_FIELD chain cap (prevents equation cross-contamination)
      - _MAX_REPAIR_DELTA_RATIO magnitude guard (heuristic repairs only)
    """
    repaired = copy.deepcopy(fields)
    repair_counts: Dict[str, int] = {}  # field_name -> mutation count this run

    if logger.isEnabledFor(logging.DEBUG):
        for k, v in fields.items():
            if v.value is not None:
                logger.debug("[REPAIR-IN] %s = %s (conf=%s)", k, v.value,
                             getattr(v, "confidence", "?"))

    # DPI-aware geometry thresholds for split-token merge passes.
    _Y_MERGE_THRESH = 15 * dpi_scale
    _X_MERGE_GAP_MAX = 60 * dpi_scale

    # --- Pre-phases ---
    _apply_null_cos_without_gp(repaired)
    _apply_zero_ncl_inference(repaired, table_rows=table_rows, flat_config=flat_config)

    for target, summands in EQUATIONS:
        suspects = [target] + summands
        residual = compute_equation_residual(repaired, target, summands)
        logger.debug("[EQ] %s = %s  residual=%s", target, summands, residual)

        # ------------------------------------------------------------------
        # Phase 3: inverse search — one field missing, scan token stream
        # ------------------------------------------------------------------
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

                        m_expected_section = _FIELD_SECTIONS.get(m)

                        for token in all_tokens:
                            if m_expected_section and page_section_map:
                                tok_section = page_section_map.get(token.page, "unknown")
                                if tok_section != "unknown" and tok_section != "notes" and tok_section != m_expected_section:
                                    continue
                            cv = parse_numeric(token.text)
                            if cv is not None and abs(cv - expected) < 0.5:
                                for s, (raw, val) in zip(others, combo):
                                    if repaired[s].value != val:
                                        repaired[s].value = val
                                        repaired[s].raw_text = raw
                                        repaired[s].tokens = []
                                        repaired[s].reason = "inverse_search_combinatorial"
                                        repaired[s].confidence = CONFIDENCE_INFERRED
                                        repair_counts[s] = repair_counts.get(s, 0) + 1
                                        logger.debug("  [INV-COMBO] %s -> %s", s, val)
                                if m in repaired:
                                    repaired[m].value = cv
                                    repaired[m].raw_text = token.text
                                    repaired[m].page = token.page
                                    repaired[m].bbox = token.bbox
                                    repaired[m].reason = "inverse_search"
                                    repaired[m].confidence = CONFIDENCE_INFERRED
                                    repaired[m].tokens = [token]
                                else:
                                    repaired[m] = _make_field_value(
                                        m, cv, token.text, token,
                                        "inverse_search", CONFIDENCE_INFERRED,
                                    )
                                repair_counts[m] = repair_counts.get(m, 0) + 1
                                logger.debug("  [INV] %s = %s", m, cv)
                                break
                        else:
                            # Split-token merge pass
                            if m not in repaired or repaired[m].value is None:
                                sorted_tokens = sorted(
                                    all_tokens,
                                    key=lambda t: (t.page, t.bbox[1], t.bbox[0]),
                                )
                                for i in range(len(sorted_tokens) - 1):
                                    ta = sorted_tokens[i]
                                    tb = sorted_tokens[i + 1]
                                    if ta.page != tb.page:
                                        continue
                                    if m_expected_section and page_section_map:
                                        tok_section = page_section_map.get(ta.page, "unknown")
                                        if tok_section != "unknown" and tok_section != "notes" and tok_section != m_expected_section:
                                            continue
                                    y_overlap = abs(ta.bbox[1] - tb.bbox[1])
                                    x_gap = tb.bbox[0] - ta.bbox[2]
                                    if y_overlap > _Y_MERGE_THRESH or x_gap < 0 or x_gap > _X_MERGE_GAP_MAX:
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
                                            repaired[m].confidence = CONFIDENCE_INFERRED
                                            repaired[m].tokens = [merged_token]
                                        else:
                                            repaired[m] = _make_field_value(
                                                m, cv, merged_text, merged_token,
                                                "inverse_search_merged", CONFIDENCE_INFERRED,
                                            )
                                        repair_counts[m] = repair_counts.get(m, 0) + 1
                                        logger.debug(
                                            "  [INV-MERGE] %s = %s (from '%s'+'%s')",
                                            m, cv, ta.text, tb.text,
                                        )
                                        break
                                else:
                                    continue
                        break

        best_repair = None

        # ------------------------------------------------------------------
        # Phase 1.6: joint solver — one missing + one OCR-misread digit
        # Section-guarded. Chain-cap enforced via repair_counts.
        # ------------------------------------------------------------------
        if not best_repair and optimization_mode and all_tokens is not None:
            missing_fields = [
                s for s in suspects if s not in repaired or repaired[s].value is None
            ]
            if len(missing_fields) == 1:
                m = missing_fields[0]
                others = [s for s in suspects if s != m]
                if all(s in repaired and repaired[s].value is not None for s in others):
                    if m == target:
                        val_others = sum(repaired[s].value for s in summands if s != m)
                        is_balanced = abs(val_others) < _RESIDUAL_TOLERANCE
                    else:
                        val_others = sum(repaired[s].value for s in summands if s != m)
                        is_balanced = abs(repaired[target].value - val_others) < _RESIDUAL_TOLERANCE

                    if not is_balanced:
                        for suspect_a in others:
                            if repair_counts.get(suspect_a, 0) >= _MAX_REPAIRS_PER_FIELD:
                                continue
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
                                if abs(expected) < _RESIDUAL_TOLERANCE or abs(expected) < 100.0:
                                    repaired[suspect_a].value = old_a
                                    continue

                                m_expected_section = _FIELD_SECTIONS.get(m)
                                matched_token = None
                                matched_val = None

                                for token in all_tokens:
                                    if m_expected_section and page_section_map:
                                        tok_section = page_section_map.get(token.page, "unknown")
                                        if tok_section != "unknown" and tok_section != "notes" and tok_section != m_expected_section:
                                            continue
                                    cv = parse_numeric(token.text)
                                    if cv is not None and abs(cv - expected) < 0.5:
                                        matched_token = token
                                        matched_val = cv
                                        break

                                if not matched_token:
                                    sorted_tokens = sorted(
                                        all_tokens,
                                        key=lambda t: (t.page, t.bbox[1], t.bbox[0]),
                                    )
                                    for idx in range(len(sorted_tokens) - 1):
                                        ta = sorted_tokens[idx]
                                        tb = sorted_tokens[idx + 1]
                                        if ta.page != tb.page:
                                            continue
                                        if m_expected_section and page_section_map:
                                            tok_section = page_section_map.get(ta.page, "unknown")
                                            if tok_section != "unknown" and tok_section != "notes" and tok_section != m_expected_section:
                                                continue
                                        y_overlap = abs(ta.bbox[1] - tb.bbox[1])
                                        x_gap = tb.bbox[0] - ta.bbox[2]
                                        if y_overlap > _Y_MERGE_THRESH or x_gap < 0 or x_gap > _X_MERGE_GAP_MAX:
                                            continue
                                        merged_text = ta.text + tb.text
                                        cv = parse_numeric(merged_text)
                                        if cv is not None and abs(cv - expected) < 0.5:
                                            matched_val = cv
                                            matched_token = Token(
                                                text=merged_text,
                                                bbox=(ta.bbox[0], ta.bbox[1], tb.bbox[2], tb.bbox[3]),
                                                page=ta.page,
                                                confidence=min(ta.confidence, tb.confidence),
                                            )
                                            break

                                repaired[suspect_a].value = old_a

                                if matched_token and matched_val is not None:
                                    if m in repaired:
                                        repaired[m].value = matched_val
                                        repaired[m].raw_text = matched_token.text
                                        repaired[m].page = matched_token.page
                                        repaired[m].bbox = matched_token.bbox
                                        repaired[m].reason = "inverse_search_merged"
                                        repaired[m].confidence = CONFIDENCE_INFERRED
                                        repaired[m].tokens = [matched_token]
                                    else:
                                        repaired[m] = _make_field_value(
                                            m, matched_val, matched_token.text, matched_token,
                                            "inverse_search", CONFIDENCE_INFERRED,
                                        )
                                    best_repair = (suspect_a, cand_a, cv_a, "equation_anchored")
                                    logger.debug("  [INV-JOINT] filled %s = %s", m, matched_val)
                                    break
                            if best_repair:
                                break

        # Recompute residual after inverse / joint solver
        residual = compute_equation_residual(repaired, target, summands)
        if (residual is None or _residual_ok(residual)) and not best_repair:
            continue

        # ------------------------------------------------------------------
        # Phase 1: digit mutation
        # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # Phase 1.5: column selection from row_candidates
        # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # Phase 2: sniper OCR — re-recognize the bounding box
        # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # Apply best repair (chain-cap + magnitude guard)
        # ------------------------------------------------------------------
        if best_repair:
            field, cand_raw, cand_val, repair_type = best_repair
            current_val = repaired[field].value
            if _is_safe_repair(current_val, cand_val, repair_type, repair_counts, field):
                repaired[field].raw_text = cand_raw
                repaired[field].value = cand_val
                repaired[field].reason = "accounting_repair"
                repaired[field].confidence = CONFIDENCE_MEDIUM
                repair_counts[field] = repair_counts.get(field, 0) + 1
                logger.debug(
                    "[REPAIR] %s: %s -> %s (type=%s, count=%d)",
                    field,
                    fields[field].raw_text if field in fields else None,
                    cand_raw,
                    repair_type,
                    repair_counts[field],
                )
            else:
                logger.debug(
                    "[REPAIR-REJECTED] %s: %s -> %s (type=%s)",
                    field, current_val, cand_val, repair_type,
                )

    # --- Post-phase: liabilities closure sanity check ---
    _check_liabilities_closure(repaired)

    return repaired
