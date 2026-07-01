import copy
import itertools
import logging
from typing import Any, Dict, List, Optional, Tuple

from .cell_ocr import targeted_ocr
from .models import FieldValue, Token
from .value_parser import parse_numeric

logger = logging.getLogger(__name__)

# Tolerance for floating-point residual checks.
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


# Maximum allowed change ratio for a repair.
_MAX_REPAIR_DELTA_RATIO = 0.15  # 15%

def _is_safe_repair(current_val: Optional[float], proposed_val: float, repair_type: str = "digit_mutation") -> bool:
    """
    Returns True if the proposed repair is safe.
    """
    if repair_type in ("sign_flip", "column_selection"):
        return True
    if current_val is None or current_val == 0.0:
        return True

    delta_ratio = abs(proposed_val - current_val) / abs(current_val)
    return delta_ratio <= _MAX_REPAIR_DELTA_RATIO


def generate_candidates(raw_text: str) -> List[str]:
    """
    Given a raw numeric string, generate slight mutations that fix common OCR errors.
    """
    candidates = []
    if not raw_text:
        return candidates

    clean = raw_text.replace(" ", "")

    for i, char in enumerate(clean):
        if char in CONFUSION_SET:
            for alt in CONFUSION_SET[char]:
                candidates.append(clean[:i] + alt + clean[i + 1 :])

    digits_only = "".join(c for c in clean if c.isdigit())
    if digits_only:
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


def _apply_zero_ncl_inference(fields: Dict[str, FieldValue]) -> None:
    """
    Zero-NCL inference rule:
    If Total Liabilities is known, Non-Current Liabilities is null, and
    Current Liabilities is null, then Current Liabilities = Total Liabilities.

    This handles SAMPLE1-style balance sheets where a company has no non-current
    liabilities section at all — the single liability line IS the total.
    We do NOT infer Non-Current Liabilities = 0 to avoid polluting the equation
    engine; we simply set Current = Total so the extractor reports it correctly.
    """
    total_fv = fields.get("Total Liabilities")
    ncl_fv = fields.get("Non-Current Liabilities")
    cl_fv = fields.get("Current Liabilities")

    total_known = total_fv is not None and total_fv.value is not None
    ncl_null = ncl_fv is None or ncl_fv.value is None
    cl_null = cl_fv is None or cl_fv.value is None

    if total_known and ncl_null and cl_null:
        logger.debug(
            "[ZERO-NCL] No NCL and no CL found — inferring Current Liabilities = Total Liabilities = %s",
            total_fv.value,
        )
        inferred = FieldValue(
            name="Current Liabilities",
            value=total_fv.value,
            raw_text=total_fv.raw_text,
            page=total_fv.page,
            tokens=total_fv.tokens,
            bbox=total_fv.bbox,
            valid=True,
            reason="zero_ncl_inference",
        )
        fields["Current Liabilities"] = inferred


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

    Pre-phase: Zero-NCL Inference — if no non-current liabilities exist and
    current liabilities are also missing, infer CL = Total Liabilities.
    """
    repaired_fields = copy.deepcopy(fields)

    logger.debug("--- REPAIR ENGINE: current field values ---")
    for k, v in fields.items():
        if v.value is not None:
            logger.debug("Field: %s = %s", k, v.value)
    logger.debug("------------------------------------------")

    # --- Pre-phase: Zero-NCL Inference ---
    _apply_zero_ncl_inference(repaired_fields)

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
                            continue

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
                                        repaired_fields[s].tokens = []
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

        # Recompute residual after potential inverse-search fill
        residual = compute_equation_residual(repaired_fields, target, summands)
        if residual is None or _residual_ok(residual):
            continue

        best_repair = None

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
                    best_repair = (suspect, cand, cand_val, "digit_mutation")

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
                            best_repair = (suspect, cand_raw, cand_val, "column_selection")
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
                            rtype = "sign_flip" if (old_val is not None and new_val is not None and old_val * new_val < 0) else "digit_mutation"
                            best_repair = (suspect, new_text, new_val, rtype)
                        fv.value = old_val
                        if best_repair:
                            break

        if best_repair:
            field, cand_raw, cand_val, repair_type = best_repair
            current_val = repaired_fields[field].value
            if not _is_safe_repair(current_val, cand_val, repair_type):
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
