import copy
import itertools
import logging
from typing import Any, Dict, List, Optional, Tuple

from .cell_ocr import targeted_ocr
from .models import FieldValue, Token
from .value_parser import parse_numeric

logger = logging.getLogger(__name__)

# Tolerance for floating-point residual checks.
# Using exact == 0.0 is unsafe due to float representation errors.
_RESIDUAL_TOLERANCE = 1e-2

EQUATIONS = [
    # (Target, [Summands])
    # Note: summands are additive. Cost of Sales is negative so Revenue + CoS = GP.
    ("Gross Profit/Loss", ["Revenue", "Cost of Sales"]),
    ("Current Assets", ["Cash and Cash Equivalents", "Trade Receivables"]),
    ("Total Assets", ["Current Assets", "Non-Current Assets"]),
    ("Total Liabilities", ["Current Liabilities", "Non-Current Liabilities"]),
    ("Total Equity", ["Paid Up Capital", "Retained Earnings"]),
    # Balance sheet identity
    ("Total Assets", ["Total Liabilities", "Total Equity"]),
]

CONFUSION_SET = {
    "0": ["8", "6", "9"],
    "1": ["7"],
    "2": ["Z", "7"],
    "3": ["8"],
    "4": ["A"],
    "5": ["S", "6", "8"],
    "6": ["5", "8", "0"],
    "7": ["1"],
    "8": ["0", "3", "6", "9", "S"],
    "9": ["0", "8"],
}


def compute_equation_residual(
    fields: Dict[str, FieldValue], target: str, summands: List[str]
) -> Optional[float]:
    """
    Computes |Target - sum(Summands)|. Returns None if any required field is missing.
    """
    if target not in fields or fields[target].value is None:
        return None

    sum_val = 0.0
    for s in summands:
        if s not in fields or fields[s].value is None:
            return None
        sum_val += fields[s].value

    return abs(fields[target].value - sum_val)


def _residual_ok(residual: Optional[float]) -> bool:
    """Return True if residual is effectively zero within tolerance."""
    return residual is not None and abs(residual) < _RESIDUAL_TOLERANCE


# Maximum allowed change ratio for a repair. If a proposed repair would change
# a field's value by more than this fraction, REJECT the repair rather than
# cascade-destroying correct fields to satisfy a broken equation.
_MAX_REPAIR_DELTA_RATIO = 0.15  # 15% — tight enough to catch wrong-year extractions

def _is_safe_repair(current_val: Optional[float], proposed_val: float) -> bool:
    """
    Return True if replacing current_val with proposed_val is a plausible
    OCR correction rather than a wrong-year or wrong-field substitution.
    
    Rule: If the proposed value is more than 15% different from the current
    value, reject the repair. This prevents the engine from overwriting
    trade receivables = 74,677 with 113,718 just because it satisfies
    a broken equation — a 52% change is not an OCR error.
    """
    if current_val is None or current_val == 0.0:
        return True  # No current value — accept any proposed value
    delta_ratio = abs(proposed_val - current_val) / abs(current_val)
    return delta_ratio <= _MAX_REPAIR_DELTA_RATIO


def generate_candidates(raw_text: str) -> List[str]:
    """
    Given a raw numeric string, generate slight mutations that fix common OCR errors.
    """
    candidates = []
    if not raw_text:
        return candidates

    # Remove whitespace
    clean = raw_text.replace(" ", "")

    # 1. Flip digits
    for i, char in enumerate(clean):
        if char in CONFUSION_SET:
            for alt in CONFUSION_SET[char]:
                candidates.append(clean[:i] + alt + clean[i + 1 :])

    # 2. Add/remove trailing zero
    digits_only = "".join(c for c in clean if c.isdigit())
    if digits_only:
        if clean.endswith("0"):
            candidates.append(clean[:-1])
        candidates.append(clean + "0")

    # 3. Add/remove negative sign/brackets
    if "(" in clean and ")" in clean:
        candidates.append(clean.replace("(", "").replace(")", ""))
    elif "-" in clean:
        candidates.append(clean.replace("-", ""))
    else:
        candidates.append("-" + clean)

    return list(set(candidates))


def _make_field_value(
    name: str, val: float, raw_text: str, token: Optional[Token], reason: str
) -> FieldValue:
    """Helper to create a FieldValue from an inverse-search result."""
    return FieldValue(
        name=name,
        value=val,
        raw_text=raw_text,
        page=token.page if token else None,
        tokens=[token] if token else [],
        bbox=token.bbox if token else None,
        valid=True,
        reason=reason,
    )


def apply_math_repairs(
    fields: Dict[str, FieldValue],
    processed_images: Dict[int, Any],
    all_tokens: List[Token] = None,
    optimization_mode: bool = False,
) -> Dict[str, FieldValue]:
    """
    Repair fields using accounting math constraints and targeted OCR.
    Three phases:
      1. Combinatorial Math Search — flip single digits to make residual zero
      2. Sniper OCR — re-OCR the suspicious cell and test
      3. Inverse Search — if only one field is missing in an equation, solve for it
    """
    repaired_fields = copy.deepcopy(fields)

    logger.debug("--- REPAIR ENGINE: current field values ---")
    for k, v in fields.items():
        if v.value is not None:
            logger.debug("Field: %s = %s", k, v.value)
    logger.debug("------------------------------------------")

    for target, summands in EQUATIONS:
        suspects = [target] + summands

        residual = compute_equation_residual(repaired_fields, target, summands)
        logger.debug("Checking eq: %s = %s -> Residual = %s", target, summands, residual)

        # --- Phase 3 first: fill in a missing field via inverse search ---
        if optimization_mode:
            missing_suspects = [
                s
                for s in suspects
                if s not in repaired_fields or repaired_fields[s].value is None
            ]

            if len(missing_suspects) > 1 and all_tokens is not None:
                assume_zero_cands = {"Non-Current Assets", "Non-Current Liabilities"}
                remaining_missing = [s for s in missing_suspects if s not in assume_zero_cands]
                
                if len(remaining_missing) == 1:
                    missing = remaining_missing[0]
                    all_others_valid = all(
                        s in repaired_fields and repaired_fields[s].value is not None
                        for s in suspects
                        if s not in missing_suspects
                    )
                    
                    if all_others_valid:
                        # Artificially set the assume_zero_cands to 0.0
                        for z in missing_suspects:
                            if z in assume_zero_cands:
                                if z in repaired_fields:
                                    repaired_fields[z].value = 0.0
                                    repaired_fields[z].raw_text = "0"
                                    repaired_fields[z].reason = "assumed_zero_for_inverse_search"
                                else:
                                    repaired_fields[z] = _make_field_value(
                                        z, 0.0, "0", None, "assumed_zero_for_inverse_search"
                                    )
                        missing_suspects = [missing]

            if len(missing_suspects) == 1 and all_tokens is not None:
                missing = missing_suspects[0]
                all_others_valid = all(
                    s in repaired_fields and repaired_fields[s].value is not None
                    for s in suspects
                    if s != missing
                )

                if all_others_valid:
                    other_suspects = [s for s in suspects if s != missing]
                    cand_lists = []
                    for s in other_suspects:
                        fv = repaired_fields[s]
                        cands = [(fv.raw_text, fv.value)]
                        if fv.row_candidates:
                            for cr, cv in fv.row_candidates:
                                if cv != fv.value:
                                    cands.append((cr, cv))
                        cand_lists.append(cands)

                    best_combo = None
                    best_expected = None
                    
                    for cand_combo in itertools.product(*cand_lists):
                        assignment = {
                            s: val for s, (raw, val) in zip(other_suspects, cand_combo)
                        }

                        if missing == target:
                            expected_val = sum(assignment[s] for s in summands)
                        else:
                            target_val = assignment[target]
                            other_summands_sum = sum(
                                assignment[s] for s in summands if s != missing
                            )
                            expected_val = target_val - other_summands_sum

                        if abs(expected_val) < _RESIDUAL_TOLERANCE:
                            if missing in repaired_fields:
                                repaired_fields[missing].value = 0.0
                                repaired_fields[missing].raw_text = "0"
                                repaired_fields[missing].reason = "inferred_zero_from_equation"
                            else:
                                repaired_fields[missing] = FieldValue(
                                    name=missing,
                                    value=0.0,
                                    raw_text="0",
                                    page=None,
                                    tokens=[],
                                    bbox=None,
                                    valid=True,
                                    reason="inferred_zero_from_equation",
                                )
                            found_match = True
                            break

                        found_match = False
                        for token in all_tokens:
                            cand_val = parse_numeric(token.text)
                            if (
                                cand_val is not None
                                and abs(cand_val - expected_val) < 0.5
                            ):
                                for s, (raw, val) in zip(other_suspects, cand_combo):
                                    if repaired_fields[s].value != val:
                                        repaired_fields[s].value = val
                                        repaired_fields[s].raw_text = raw
                                        repaired_fields[s].reason = (
                                            "inverse_search_combinatorial"
                                        )
                                        logger.debug(
                                            "  [INVERSE SEARCH] Updated %s to %s to satisfy eq",
                                            s, val,
                                        )

                                if missing in repaired_fields:
                                    fv = repaired_fields[missing]
                                    fv.value = cand_val
                                    fv.raw_text = token.text
                                    fv.page = token.page
                                    fv.bbox = token.bbox
                                    fv.reason = "inverse_search"
                                    fv.tokens = [token]
                                else:
                                    repaired_fields[missing] = _make_field_value(
                                        missing,
                                        cand_val,
                                        token.text,
                                        token,
                                        "inverse_search",
                                    )
                                logger.debug(
                                    "  [INVERSE SEARCH] %s = %s (from token '%s')",
                                    missing, cand_val, token.text,
                                )
                                found_match = True
                                break
                        
                        if found_match:
                            break
                            
                        # Save the first combination for fallback if no tokens match any combination
                        if best_combo is None:
                            best_combo = cand_combo
                            best_expected = expected_val
                            
                    if not found_match and best_combo is not None:
                        if missing in repaired_fields:
                            fv = repaired_fields[missing]
                            fv.value = best_expected
                            fv.raw_text = str(int(best_expected)) if best_expected.is_integer() else str(best_expected)
                            fv.reason = "inferred_implicit_sum"
                        else:
                            repaired_fields[missing] = _make_field_value(
                                missing,
                                best_expected,
                                str(int(best_expected)) if best_expected.is_integer() else str(best_expected),
                                None,
                                "inferred_implicit_sum",
                            )
                        for s, (raw, val) in zip(other_suspects, best_combo):
                            if repaired_fields[s].value != val:
                                repaired_fields[s].value = val
                                repaired_fields[s].raw_text = raw
                                repaired_fields[s].reason = "inverse_search_combinatorial_implicit"

        # Recompute residual after potential inverse-search fill
        residual = compute_equation_residual(repaired_fields, target, summands)
        if residual is None or _residual_ok(residual):
            continue

        # We have a non-zero residual. Try to repair the suspect fields.
        best_repair = None  # (field_name, new_raw_text, new_float_val)

        # --- Phase 1: Combinatorial Math Search ---
        for suspect in suspects:
            if suspect not in repaired_fields or repaired_fields[suspect].value is None:
                continue

            original_raw = repaired_fields[suspect].raw_text
            if original_raw is None:
                continue

            candidates = generate_candidates(original_raw)
            for cand in candidates:
                cand_val = parse_numeric(cand)
                if cand_val is None:
                    continue

                old_val = repaired_fields[suspect].value
                repaired_fields[suspect].value = cand_val

                new_res = compute_equation_residual(repaired_fields, target, summands)
                if _residual_ok(new_res):
                    best_repair = (suspect, cand, cand_val)

                repaired_fields[suspect].value = old_val
                if best_repair:
                    break
            if best_repair:
                break

        # --- Phase 1.5: Column Selection ---
        if not best_repair:
            for suspect in suspects:
                if (
                    suspect not in repaired_fields
                    or repaired_fields[suspect].value is None
                ):
                    continue
                fv = repaired_fields[suspect]
                if fv.row_candidates:
                    for cand_raw, cand_val in fv.row_candidates:
                        if cand_val == fv.value:
                            continue
                        old_val = fv.value
                        fv.value = cand_val
                        new_res = compute_equation_residual(
                            repaired_fields, target, summands
                        )
                        if _residual_ok(new_res):
                            best_repair = (suspect, cand_raw, cand_val)
                            logger.debug(
                                "  [COLUMN REPAIR] %s selected alternate column value: %s",
                                suspect, cand_val,
                            )
                        fv.value = old_val
                        if best_repair:
                            break
                if best_repair:
                    break

        # --- Phase 2: Sniper OCR ---
        if not best_repair:
            for suspect in suspects:
                if (
                    suspect not in repaired_fields
                    or repaired_fields[suspect].value is None
                ):
                    continue

                fv = repaired_fields[suspect]
                if fv.page in processed_images and fv.bbox is not None:
                    img = processed_images[fv.page]
                    new_text = targeted_ocr(img, fv.bbox)
                    new_val = parse_numeric(new_text)
                    if new_val is not None and new_val != fv.value:
                        old_val = fv.value
                        fv.value = new_val
                        new_res = compute_equation_residual(
                            repaired_fields, target, summands
                        )
                        if _residual_ok(new_res):
                            best_repair = (suspect, new_text, new_val)
                        fv.value = old_val
                        if best_repair:
                            break

        if best_repair:
            field, cand_raw, cand_val = best_repair
            current_val = repaired_fields[field].value
            if not _is_safe_repair(current_val, cand_val):
                logger.debug(
                    "  [REPAIR REJECTED] %s: proposed %s would change %.1f%% from %s — too destructive",
                    field, cand_raw,
                    abs(cand_val - (current_val or 0)) / (abs(current_val) + 1e-9) * 100,
                    current_val,
                )
            else:
                repaired_fields[field].raw_text = cand_raw
                repaired_fields[field].value = cand_val
                repaired_fields[field].reason = "accounting_repair"
                logger.debug(
                    "  [REPAIR] %s: %s -> %s",
                    field,
                    fields.get(field, None) and fields[field].raw_text,
                    cand_raw,
                )


    return repaired_fields
