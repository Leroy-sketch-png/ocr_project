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
        return fuzz.ratio(query.lower(), desc.lower())

    intersection = q_tokens.intersection(d_tokens)
    if not intersection:
        return fuzz.ratio(query.lower(), desc.lower())

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
        return max(base, fuzz.ratio(query.lower(), desc.lower()))
    elif recall >= 0.5:
        # Partial overlap — keep a small extra-word penalty but don't kill the score
        score = (recall * 90) - (extra_words * 5)
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
    Classify an auditor's opinion block into one of the four standard labels.
    
    Handles both:
    - Compact phrases: "qualified opinion", "adverse opinion"
    - Split mentions: "our opinion is unmodified" (no adjacent "opinion")
    """
    t = text.lower()

    # Check for disclaimer first (most specific — a disclaimer IS NOT a qualified)
    if "disclaim" in t:
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
                    if val == 0.0 and field_name in results and best_kw_score - current_best_score < 5:
                        existing_val = parse_numeric(results[field_name].raw_text)
                        if existing_val is not None and abs(existing_val) > 0:
                            should_update = False
                        else:
                            should_update = True
                    else:
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
                results["Auditor's Opinion"] = FieldValue(
                    name="Auditor's Opinion",
                    value=opinion_value,
                    raw_text=kw,
                    page=block.page,
                    tokens=block.tokens,
                    bbox=compute_bbox(block.tokens),
                    valid=True,
                    reason=None,
                    field_label=" ".join(t.text for t in block.tokens[:12]),
                )
                found_opinion = True
                break

    return results
