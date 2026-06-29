import re
from typing import Any, Dict, List, Optional, Tuple

import yaml
from thefuzz import fuzz

from .models import FieldValue, TableRow, TextBlock, Token
from .value_parser import parse_numeric


def compute_match_score(query: str, desc: str) -> float:
    """
    Computes a match score based on token overlap. Penalizes extra modifiers
    unless they are present in the query.
    """
    q_clean = re.sub(r"[^a-z0-9\s]", "", query.lower())
    d_clean = re.sub(r"[^a-z0-9\s]", "", desc.lower())

    q_tokens = set(q_clean.split())
    d_tokens = set(d_clean.split())

    if not q_tokens or not d_tokens:
        return 0.0

    intersection = q_tokens.intersection(d_tokens)
    if not intersection:
        return fuzz.ratio(query.lower(), desc.lower())

    recall = len(intersection) / len(q_tokens)
    extra_words = len(d_tokens) - len(intersection)

    if recall == 1.0:
        score = 100 - (extra_words * 10)
        return max(score, fuzz.ratio(query.lower(), desc.lower()))
    elif recall >= 0.5:
        score = (recall * 100) - (extra_words * 10)
        return max(score, fuzz.ratio(query.lower(), desc.lower()))

    return fuzz.ratio(query.lower(), desc.lower())


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
    Convert a matched auditor paragraph into one of the assignment labels.
    """
    text_lower = text.lower()
    if "disclaimer of opinion" in text_lower:
        return "Disclaimer"
    if "adverse opinion" in text_lower:
        return "Adverse"
    if "qualified opinion" in text_lower:
        return "Qualified"
    if "unqualified opinion" in text_lower or "unmodified opinion" in text_lower:
        return "Unqualified"
    return None


def extract_fields(
    table_rows: List[TableRow],
    text_blocks: List[TextBlock],
    config: Dict[str, Any],
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
                best_cell_idx = 0
                for idx, cell_text in enumerate(row.cells):
                    clean_text = (
                        cell_text.replace(",", "")
                        .replace(".", "")
                        .replace(" ", "")
                        .strip()
                    )
                    if idx == 0 and clean_text.isdigit() and len(clean_text) <= 2:
                        continue
                    best_cell_idx = idx
                    break

                raw_text = row.cells[best_cell_idx] if row.cells else None
                val = parse_numeric(raw_text)

                current_best_score = best_scores[field_name]
                should_update = False

                if best_kw_score > current_best_score:
                    should_update = True
                elif best_kw_score == current_best_score:
                    if val is not None:
                        if field_name in results:
                            existing_val = parse_numeric(results[field_name].raw_text)
                            if existing_val is not None:
                                if abs(val) > abs(existing_val):
                                    should_update = True
                            else:
                                should_update = True

                    if field_name in results and not should_update:
                        for cell_text in row.cells:
                            cval = parse_numeric(cell_text)
                            if cval is not None:
                                results[field_name].row_candidates.append(
                                    (cell_text, cval)
                                )

                if should_update:
                    best_scores[field_name] = best_kw_score
                    tokens_for_field = (
                        row.cell_tokens[best_cell_idx] if row.cell_tokens else []
                    )

                    row_cands = []
                    for cell_text in row.cells:
                        cval = parse_numeric(cell_text)
                        if cval is not None:
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
    # The outer loop must break as soon as a valid opinion is found so that
    # a later, possibly lower-quality block cannot overwrite an already-correct result.
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
                    opinion_value = normalize_auditor_opinion(kw)
                results["Auditor's Opinion"] = FieldValue(
                    name="Auditor's Opinion",
                    value=opinion_value,
                    raw_text=kw,
                    page=block.page,
                    tokens=block.tokens,
                    bbox=compute_bbox(block.tokens),
                    valid=opinion_value is not None,
                    reason=None if opinion_value is not None else "opinion_parse_error",
                    field_label="Auditor's Opinion",
                )
                found_opinion = True
                break

    return results
