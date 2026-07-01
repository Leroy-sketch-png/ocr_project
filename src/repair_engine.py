import copy
import itertools
import logging
from typing import Any, Dict, List, Optional

from .cell_ocr import targeted_ocr
from .field_extractor import compute_match_score
from .models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_INFERRED,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    FieldValue,
    TableRow,
    Token,
)
from .value_parser import parse_numeric

logger = logging.getLogger(__name__)

_RESIDUAL_TOLERANCE = 1e-2

# ---------------------------------------------------------------------------
# Accounting equations
# ---------------------------------------------------------------------------
# All summands are additive. Signed fields (e.g. Cost of Sales, which is
# negative) carry their sign in the extracted value so arithmetic is uniform.
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
# Used by Phase 1.6 and Phase 3 to reject tokens found in the wrong section.
# Exception: tokens on 'notes' pages are always allowed through as a fallback
# because some fields (e.g. Income Tax Expense) are only disclosed in notes.
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

# Maximum fractional change allowed for a heuristic (non-equation-anchored) repair.
_MAX_REPAIR_DELTA_RATIO = 0.15

# A field may only be mutated once across all equations in a single repair run.
# Prevents equation cross-contamination (fixing field A for eq-1 breaking eq-2).
_MAX_REPAIRS_PER_FIELD = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_equation_residual(
    fields: Dict[str, FieldValue], target: str, summands: List[str]
) -> Optional[float]:
    """Return |target - sum(summands)|, or None if any participant is missing."""
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
    1. Chain cap: reject if this field has already been mutated once this run.
    2. Structural repairs (sign_flip, column_selection, equation_anchored)
       bypass the magnitude ratio guard — they are mathematically proven.
    3. Heuristic repairs: reject if change exceeds _MAX_REPAIR_DELTA_RATIO.
    """
    if repair_counts.get(field, 0) >= _MAX_REPAIRS_PER_FIELD:
        logger.debug(
            "[REPAIR-CAP] %s already repaired %d time(s) — rejecting",
            field, repair_counts[field],
        )
        return False
    if repair_type in ("sign_flip", "column_selection", "equation_anchored"):
        return True
    if current_val is None or abs(current_val) < 1e-9:
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
    field_label: Optional[str] = None,
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
        field_label=field_label,
    )


# ---------------------------------------------------------------------------
# Pre-phase rules
# ---------------------------------------------------------------------------

def _apply_null_cos_without_gp(fields: Dict[str, FieldValue]) -> None:
    """Null Cost of Sales when no Gross Profit line exists.

    CoS is only meaningful when a gross profit concept exists. If GP is absent
    the CoS match is almost certainly a false positive from an operating-expense
    row in a services-only P&L. Nulling it prevents downstream equation corruption.
    """
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

    Handles documents where there are no non-current liabilities at all —
    the single liabilities line IS the total.

    Structural guard: suppressed if any balance-sheet row scores >= 80
    against any NCL keyword. Score >= 80 means NCL exists structurally but
    was merely unmatched at the normal extraction threshold. In that case
    inferring CL = TL would silently corrupt the balance sheet.
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
        for row in table_rows:
            desc_lower = row.description.lower()
            for kw in ncl_kws:
                if compute_match_score(kw, desc_lower) >= 80:
                    logger.debug(
                        "[ZERO-NCL] Suppressed — structural NCL candidate (score>=80): '%s'",
                        row.description,
                    )
                    return

    logger.debug("[ZERO-NCL] Inferring CL = TL = %s", tl.value)
    # Try to locate the actual CL row for correct bbox/evidence
    raw_text = tl.raw_text
    page = tl.page
    tokens = tl.tokens
    bbox = tl.bbox
    if table_rows is not None:
        for row in table_rows:
            desc = row.description.strip().lower()
            if desc in ("total liabilities", "total ilities"):
                for ci, ct in enumerate(row.cells):
                    try:
                        cv = float(ct.replace(",", ""))
                        if abs(cv - tl.value) < 0.5:
                            raw_text = ct
                            page = row.page
                            if ci < len(row.cell_tokens) and row.cell_tokens[ci]:
                                toks = row.cell_tokens[ci]
                                xs = [t.bbox[0] for t in toks]
                                ys = [t.bbox[1] for t in toks]
                                xe = [t.bbox[2] for t in toks]
                                ye = [t.bbox[3] for t in toks]
                                bbox = (min(xs), min(ys), max(xe), max(ye))
                            break
                    except (ValueError, AttributeError):
                        continue
                break
    fields["Current Liabilities"] = FieldValue(
        name="Current Liabilities",
        value=tl.value,
        raw_text=raw_text,
        page=page,
        tokens=tokens,
        bbox=bbox,
        valid=True,
        reason="zero_ncl_inference",
        confidence=CONFIDENCE_INFERRED,
    )


def _apply_cl_inference(
    fields: Dict[str, FieldValue],
    table_rows: Optional[List[TableRow]] = None,
) -> None:
    """
    If Current Liabilities is missing but Non-Current Liabilities and
    Total Liabilities are present, infer CL = TL - NCL.
    This handles IFRS balance sheets where CL is an unlabeled subtotal
    that appears as a row with no description text.
    """
    tl = fields.get("Total Liabilities")
    ncl = fields.get("Non-Current Liabilities")
    cl = fields.get("Current Liabilities")
    if not (
        tl is not None and tl.value is not None
        and ncl is not None and ncl.value is not None
        and (cl is None or cl.value is None)
    ):
        return

    inferred_cl = tl.value - ncl.value
    if inferred_cl < 0:
        return

    # Find the row matching this inferred value to get correct bbox/evidence
    raw_text = str(int(inferred_cl))
    page = tl.page
    tokens = tl.tokens
    bbox = tl.bbox
    if table_rows is not None:
        for row in table_rows:
            for ci, cell_text in enumerate(row.cells):
                try:
                    cv = float(cell_text.replace(",", "").replace("(", "").replace(")", "").replace(" ", ""))
                    if abs(cv - inferred_cl) < 0.5:
                        raw_text = cell_text
                        page = row.page
                        if ci < len(row.cell_tokens) and row.cell_tokens[ci]:
                            tokens = row.cell_tokens[ci]
                            xs = [t.bbox[0] for t in tokens]
                            ys = [t.bbox[1] for t in tokens]
                            xe = [t.bbox[2] for t in tokens]
                            ye = [t.bbox[3] for t in tokens]
                            bbox = (min(xs), min(ys), max(xe), max(ye))
                        break
                except (ValueError, AttributeError):
                    continue

    logger.debug("[CL-INFER] CL = TL - NCL = %s - %s = %s", tl.value, ncl.value, inferred_cl)
    fields["Current Liabilities"] = FieldValue(
        name="Current Liabilities",
        value=inferred_cl,
        raw_text=raw_text,
        page=page,
        tokens=tokens,
        bbox=bbox,
        valid=True,
        reason="cl_inference",
        confidence=CONFIDENCE_INFERRED,
    )


def _apply_zero_nca_inference(
    fields: Dict[str, FieldValue],
    table_rows: Optional[List[TableRow]] = None,
    flat_config: Optional[Dict[str, Any]] = None,
) -> None:
    """
    If Total Assets is present, but Non-Current Assets and Plant and Equipment
    are missing (None), and there is no structural NCA candidate,
    infer Non-Current Assets = 0.0 so we can solve Current Assets = Total Assets.
    """
    ta = fields.get("Total Assets")
    nca = fields.get("Non-Current Assets")
    pe = fields.get("Plant and Equipment")

    if ta is None or ta.value is None:
        return

    # Only run if both NCA and PE are missing
    if (nca is not None and nca.value is not None) or (pe is not None and pe.value is not None):
        return

    # Check for any structural NCA keywords in table rows
    if table_rows is not None and flat_config is not None:
        nca_cfg = flat_config.get("Non-Current Assets", {})
        nca_kws = nca_cfg.get("keywords", []) if isinstance(nca_cfg, dict) else []
        pe_cfg = flat_config.get("Plant and Equipment", {})
        pe_kws = pe_cfg.get("keywords", []) if isinstance(pe_cfg, dict) else []
        kws = nca_kws + pe_kws
        for row in table_rows:
            desc_lower = row.description.lower()
            for kw in kws:
                if compute_match_score(kw, desc_lower) >= 80:
                    logger.debug(
                        "[ZERO-NCA] Suppressed — structural NCA candidate (score>=80): '%s'",
                        row.description,
                    )
                    return

    logger.debug("[ZERO-NCA] Inferring NCA = 0.0")
    fields["Non-Current Assets"] = FieldValue(
        name="Non-Current Assets",
        value=0.0,
        raw_text="0",
        page=ta.page,
        tokens=[],
        bbox=None,
        valid=True,
        reason="zero_nca_inference",
        confidence=CONFIDENCE_LOW,
    )


def _check_liabilities_closure(fields: Dict[str, FieldValue]) -> None:
    """Post-repair sanity check: TL must equal CL + NCL.

    If they don't close (e.g. TL was misread and zero-NCL inference was wrong),
    downgrade TL to CONFIDENCE_LOW and emit a WARNING. This is a diagnostic
    signal only — it does not attempt further repair.
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

    Pre-phases (always run):
      0A — Null-CoS-without-GP: prevents false CoS on services-only P&L
      0B — Zero-NCL inference: CL = TL when NCL is structurally absent
           (guarded: suppressed if any row scores >= 80 on NCL keywords)

    Repair phases (per EQUATION, requires at least one field to be wrong):
      1   — Digit mutation: flip one OCR-confused character per field
      1.5 — Column selection: try alternate column values from row_candidates
      1.6 — Joint solver (optimization_mode): one field missing + one misread;
             mutate the digit, derive implied missing value, scan tokens
      2   — Sniper OCR: re-OCR the suspicious bounding box
      3   — Inverse search (optimization_mode): one field missing, scan all
             tokens (single + DPI-scaled split-merge) for the closing value

    Post-phase:
      4   — Liabilities closure sanity check: warns and downgrades confidence
             if TL != CL + NCL after all repairs
    """
    repaired = copy.deepcopy(fields)
    repair_counts = {}

    if logger.isEnabledFor(logging.DEBUG):
        for k, v in fields.items():
            if v.value is not None:
                logger.debug("[REPAIR-IN] %s = %s (conf=%s)", k, v.value,
                             getattr(v, "confidence", "?"))

    # DPI-aware geometry thresholds for split-token merge passes
    _Y_MERGE_THRESH = 15 * dpi_scale
    _X_MERGE_GAP_MAX = 60 * dpi_scale

    # Pre-phases
    _apply_null_cos_without_gp(repaired)
    _apply_zero_ncl_inference(repaired, table_rows=table_rows, flat_config=flat_config)
    _apply_cl_inference(repaired, table_rows=table_rows)
    _apply_zero_nca_inference(repaired, table_rows=table_rows, flat_config=flat_config)

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
                                # Allow: correct section, unknown, or notes (disclosed in notes)
                                if tok_section not in ("unknown", "notes", m_expected_section):
                                    continue
                            cv = parse_numeric(token.text)
                            if cv is not None and abs(cv - expected) < 0.5:
                                # Verify safety of proposed mutations
                                safe = True
                                for s, (_, val) in zip(others, combo):
                                    if repaired[s].value != val:
                                        if not _is_safe_repair(repaired[s].value, val, "inverse_search_combinatorial", repair_counts, s):
                                            safe = False
                                            break
                                if safe:
                                    m_curr = repaired[m].value if m in repaired else None
                                    if not _is_safe_repair(m_curr, cv, "inverse_search", repair_counts, m):
                                        safe = False
                                if not safe:
                                    continue

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
                                        if tok_section not in ("unknown", "notes", m_expected_section):
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
                                        m_curr = repaired[m].value if m in repaired else None
                                        if not _is_safe_repair(m_curr, cv, "inverse_search_merged", repair_counts, m):
                                            continue
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
                                        if tok_section not in ("unknown", "notes", m_expected_section):
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
                                            if tok_section not in ("unknown", "notes", m_expected_section):
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

    # Post-phase: liabilities closure sanity check
    _check_liabilities_closure(repaired)

    return repaired
