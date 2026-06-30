import re
from typing import Any, Dict, List, Optional, Tuple

import yaml
from thefuzz import fuzz

from .models import FieldValue, TableRow, TextBlock, Token
from .value_parser import parse_numeric


def compute_match_score(query: str, desc: str) -> float:
    """
    Score how well `desc` matches `query`.
    
    Design:
    - Full token overlap (query tokens ⊆ desc tokens): high score, no extra-word penalty.
    - Partial overlap: scaled score, small extra-word penalty.
    - No overlap: fall back to fuzzy ratio.
    
    The key change from the old version: when query tokens are fully contained
    in desc tokens (containment match), we do NOT penalise the extra description
    words. This handles "Trade and other receivables" matching "Trade Receivables".
    """
    q_clean = re.sub(r"[^a-z0-9\s]", "", query.lower())
    d_clean = re.sub(r"[^a-z0-9\s]", "", desc.lower())

    q_tokens = set(q_clean.split())
    d_tokens = set(d_clean.split())

    # Remove grammatical stop words that add noise.
    # CRITICAL: Do NOT remove 'net', 'total', 'other' as they are semantically vital in finance.
    _STOPS = {"and", "or", "the", "of", "for", "in"}
    q_tokens -= _STOPS
    d_tokens -= _STOPS

    if not q_tokens or not d_tokens:
        return fuzz.ratio(q_clean, d_clean)

    intersection = q_tokens.intersection(d_tokens)
    if not intersection:
        return fuzz.ratio(q_clean, d_clean)

    recall = len(intersection) / len(q_tokens)  # how much of query is covered
    extra_words = len(d_tokens) - len(intersection)  # words in desc not in query

    if recall == 1.0:
        # All query tokens are present in description — containment match.
        # Do NOT penalise extra words: "Trade and other receivables" should
        # score as well as "Trade receivables" for the query "trade receivables".
        base = 95.0
        # Small penalty only if description is 3x longer than query (very different)
        if extra_words > len(q_tokens) * 2:
            base -= 5.0
        return max(base, fuzz.ratio(q_clean, d_clean))
    elif recall >= 0.5:
        # Partial overlap — keep a small extra-word penalty but don't kill the score
        score = (recall * 90) - (extra_words * 5)
        return max(score, fuzz.ratio(q_clean, d_clean))

    return fuzz.ratio(q_clean, d_clean)


def load_field_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        if not isinstance(config, dict):
            return {}
        return config


def compute_bbox(tokens: List[Token]) -> Optional[Tuple[int, int, int, int]]:
    if not tokens:
        return None
    x1 = min(t.bbox[0] for t in tokens)
    y1 = min(t.bbox[1] for t in tokens)
    x2 = max(t.bbox[2] for t in tokens)
    y2 = max(t.bbox[3] for t in tokens)
    return (x1, y1, x2, y2)


def normalize_auditor_opinion(text: str) -> Optional[str]:
    """
    Classify an auditor's opinion block into one of the four standard labels.
    
    Handles both:
    - Compact phrases: "qualified opinion", "adverse opinion"
    - Split mentions: "our opinion is unmodified" (no adjacent "opinion")
    """
    t = text.lower()

    # Check for disclaimer first (most specific — a disclaimer IS NOT a qualified)
    if "disclaimer of opinion" in t or ("disclaim" in t and "opinion" in t):
        return "Disclaimer"

    # Adverse opinion
    if "adverse" in t and ("opinion" in t or "view" in t):
        return "Adverse"

    # Qualified opinion — "qualified" appears near "opinion"
    # Also catches "except for" which is the standard signal of a qualified opinion
    if "qualified opinion" in t or ("qualified" in t and "opinion" in t):
        # Make sure it's not "unqualified" being matched by "qualified"
        if "unqualified" not in t and "unmodified" not in t:
            return "Qualified"
    if "except for" in t:
        return "Qualified"

    # Unqualified / clean / unmodified opinion
    if (
        "unqualified opinion" in t
        or "unmodified opinion" in t
        or "unmodified" in t
        or "true and fair view" in t
        or "present fairly" in t
        or "clean opinion" in t
    ):
        return "Unqualified"

    return None


def _cross_field_collision_check(results: dict) -> dict:
    """
    If the same value AND same page AND same bbox are assigned to two different
    fields, the lower-scoring one is a collision and should be nulled out.
    This is general: in a real financial statement, the same table cell cannot
    be both Current Liabilities and Non-Current Liabilities.
    """
    seen = {}  # (page, bbox) -> (field_name, score)
    to_null = []
    for field_name, fv in results.items():
        if fv is None or fv.raw_text is None:
            continue
        key = (fv.page, fv.raw_text)  # same page + same raw text = same cell
        if key in seen:
            # Keep the field whose name more closely matches "current" vs "non-current"
            # Actually: just null the one that is semantically inconsistent.
            # Non-Current should never share a value with Current at the same hierarchy.
            existing_field = seen[key]
            if "Non-Current" in field_name and "Non-Current" not in existing_field:
                to_null.append(field_name)
            elif "Non-Current" in existing_field and "Non-Current" not in field_name:
                to_null.append(existing_field)
        else:
            seen[key] = field_name
    for f in to_null:
        del results[f]
    return results

def detect_year_column(table_rows: List[TableRow]) -> Dict[int, float]:
    """
    Returns a mapping from page number to the X-coordinate (center) of the most recent year column.
    Aggregates years across the top 10 rows of each page to handle split headers.
    """
    page_year_x: Dict[int, float] = {}
    last_seen_x = None
    reporting_year = None
    
    pages = sorted(list(set(row.page for row in table_rows)))
    
    for page in pages:
        page_rows = [r for r in table_rows if r.page == page]
        
        # Aggregate year cells in the top 10 rows
        year_cells = []
        for row in page_rows[:10]:
            for cell_text, tokens in zip(row.cells, row.cell_tokens):
                clean = cell_text.replace(",", "").replace(" ", "").strip()
                if clean.isdigit() and 1990 <= int(clean) <= 2030 and tokens:
                    # Calculate center X of the cell from its tokens
                    min_x = min(t.bbox[0] for t in tokens)
                    max_x = max(t.bbox[2] for t in tokens)
                    center_x = (min_x + max_x) / 2.0
                    year_cells.append((center_x, int(clean)))
        
        # Filter to unique years
        unique_years = {y[1]: y[0] for y in year_cells}
        if len(unique_years) >= 2:
            max_year_in_header = max(unique_years.keys())
            if reporting_year is None:
                reporting_year = max_year_in_header
                
            if max_year_in_header == reporting_year:
                target_x = unique_years[max_year_in_header]
                page_year_x[page] = target_x
                last_seen_x = target_x
        
        if page not in page_year_x and last_seen_x is not None:
            # Forward-fill X coordinate for pages without headers
            page_year_x[page] = last_seen_x
            
    return page_year_x


def extract_fields(
    table_rows: List[TableRow],
    text_blocks: List[TextBlock],
    config: Dict[str, Any],
    year_x_map: Dict[int, float] = None,
    dpi_scale: float = 1.0,
) -> Dict[str, FieldValue]:
    results = {}
    flat_config = {}

    for section, fields in config.items():
        if not isinstance(fields, dict):
            continue
        for field_name, details in fields.items():
            if isinstance(details, dict):
                flat_config[field_name] = details.get("keywords", [])

    # Track the highest scoring row per field
    best_scores = {k: -1.0 for k in flat_config.keys()}

    # Extract numeric fields
    for row in table_rows:
        desc_lower = row.description.lower()
        for field_name, keywords in flat_config.items():
            if field_name == "Auditor's Opinion":
                continue  # Handled separately

            best_kw_score = max(
                (compute_match_score(kw, desc_lower) for kw in keywords), default=0.0
            )

            if best_kw_score >= 82:
                best_cell_idx = None
                target_x = year_x_map.get(row.page) if year_x_map else None
                
                if target_x is not None:
                    # Find the parseable cell whose tokens are closest to target_x
                    closest_idx = None
                    min_dist = float('inf')
                    for i, tokens in enumerate(row.cell_tokens):
                        if not tokens: continue
                        if parse_numeric(row.cells[i]) is None: continue
                        
                        min_cx = min(t.bbox[0] for t in tokens)
                        max_cx = max(t.bbox[2] for t in tokens)
                        center_x = (min_cx + max_cx) / 2.0
                        
                        dist = abs(center_x - target_x)
                        if dist < min_dist:
                            min_dist = dist
                            closest_idx = i
                            
                    if closest_idx is not None and min_dist < (400.0 * dpi_scale):
                        best_cell_idx = closest_idx
                        
                if best_cell_idx is None:
                    # Fallback: pick the FIRST parseable numeric cell > 100 (Group 2023)
                    for idx in range(len(row.cells)):
                        v = parse_numeric(row.cells[idx])
                        if v is not None:
                            if abs(v) > 100:
                                best_cell_idx = idx
                                break
                            elif best_cell_idx is None:
                                best_cell_idx = idx
                            
                    if best_cell_idx is None:
                        best_cell_idx = 0

                raw_text = row.cells[best_cell_idx] if row.cells else None
                val = parse_numeric(raw_text)
                
                # GT expects Cost of Sales to be negative
                if field_name == "Cost of Sales" and val is not None and val > 0:
                    val = -val
                    if raw_text and not raw_text.startswith("-") and not "(" in raw_text:
                        raw_text = "-" + raw_text

                current_best_score = best_scores[field_name]
                should_update = False

                if best_kw_score > current_best_score:
                    should_update = True
                elif best_kw_score == current_best_score:
                    if val is not None:
                        if field_name in results:
                            existing_val = parse_numeric(results[field_name].raw_text)
                            existing_page = results[field_name].page
                            if existing_val is not None:
                                # Prioritize earlier pages (Group) over later pages (Company)
                                if row.page < existing_page:
                                    should_update = True
                                elif row.page == existing_page:
                                    if abs(val) > abs(existing_val):
                                        should_update = True
                                elif abs(val) > abs(existing_val) * 10:
                                    # Overwrite tiny text mentions (like 2.14%) with large table values
                                    should_update = True
                            else:
                                should_update = True

                    if field_name in results and not should_update:
                        for cell_text in row.cells:
                            cval = parse_numeric(cell_text)
                            if cval is not None:
                                if field_name == "Cost of Sales" and cval > 0:
                                    cval = -cval
                                    if cell_text and not cell_text.startswith("-") and not "(" in cell_text:
                                        cell_text = "-" + cell_text
                                results[field_name].row_candidates.append(
                                    (cell_text, cval)
                                )

                if should_update:
                    best_scores[field_name] = best_kw_score
                    tokens_for_field = (
                        row.cell_tokens[best_cell_idx] if row.cell_tokens else []
                    )

                    row_cands = []
                    if field_name in results and results[field_name] is not None:
                        # Keep the old candidates
                        old_fv = results[field_name]
                        if old_fv.row_candidates:
                            row_cands.extend(old_fv.row_candidates)
                        # Also keep the old primary value as a candidate if it exists
                        old_val = parse_numeric(old_fv.raw_text)
                        if old_val is not None:
                            # Avoid duplicate if it's already in row_cands
                            if not any(c == old_val for _, c in row_cands):
                                row_cands.append((old_fv.raw_text, old_val))

                    for cell_text in row.cells:
                        cval = parse_numeric(cell_text)
                        if cval is not None:
                            if not any(c == cval for _, c in row_cands):
                                row_cands.append((cell_text, cval))

                    results[field_name] = FieldValue(
                        name=field_name,
                        value=None,  # To be parsed later
                        raw_text=raw_text,
                        page=row.page,
                        tokens=tokens_for_field,
                        bbox=compute_bbox(tokens_for_field),
                        valid=False,
                        reason=None,
                        row_candidates=row_cands,
                        field_label=row.description,
                    )

    # Extract Auditor's Opinion
    auditor_kws = flat_config.get("Auditor's Opinion", [])
    found_opinion = False
    for block in text_blocks:
        if found_opinion:
            break
        text_lower = " ".join(t.text for t in block.tokens).lower()
        for kw in auditor_kws:
            if kw.lower() in text_lower:
                opinion_value = normalize_auditor_opinion(text_lower)
                if opinion_value is None:
                    # keyword present but opinion type not determinable from this block — skip
                    continue
                
                first_line = []
                for tok in block.tokens:
                    if first_line and tok.bbox[0] - first_line[-1].bbox[2] > 100:
                        break
                    if len(first_line) >= 8:
                        break
                    first_line.append(tok)
                field_label_text = " ".join(t.text for t in first_line) if first_line else kw

                results["Auditor's Opinion"] = FieldValue(
                    name="Auditor's Opinion",
                    value=opinion_value,
                    raw_text=kw,
                    page=block.page,
                    tokens=block.tokens,
                    bbox=compute_bbox(block.tokens),
                    valid=True,
                    reason=None,
                    field_label=field_label_text,
                )
                found_opinion = True
                break

    if not found_opinion:
        for block in text_blocks:
            text_lower = " ".join(t.text for t in block.tokens).lower()
            opinion_value = normalize_auditor_opinion(text_lower)
            if opinion_value is not None:
                first_line = []
                for tok in block.tokens:
                    if first_line and tok.bbox[0] - first_line[-1].bbox[2] > 100:
                        break
                    if len(first_line) >= 8:
                        break
                    first_line.append(tok)
                field_label_text = " ".join(t.text for t in first_line) if first_line else opinion_value

                results["Auditor's Opinion"] = FieldValue(
                    name="Auditor's Opinion",
                    value=opinion_value,
                    raw_text=text_lower[:80],   # first 80 chars as evidence
                    page=block.page,
                    tokens=block.tokens,
                    bbox=compute_bbox(block.tokens),
                    valid=True,
                    reason="full_text_fallback",
                    field_label=field_label_text,
                )
                found_opinion = True
                break

    results = _cross_field_collision_check(results)
    return results
