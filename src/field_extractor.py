from typing import Any, Dict, List, Optional, Tuple

import yaml

from .models import FieldValue, TableRow, TextBlock, Token


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

    # Extract numeric fields
    for row in table_rows:
        desc_lower = row.description.lower()
        for field_name, keywords in flat_config.items():
            if field_name == "Auditor’s Opinion":
                continue  # Handled separately

            if any(kw.lower() in desc_lower for kw in keywords):
                if field_name not in results:
                    tokens_for_field = row.cell_tokens[0] if row.cell_tokens else []
                    results[field_name] = FieldValue(
                        name=field_name,
                        value=None,  # To be parsed later
                        raw_text=row.cells[0] if row.cells else None,
                        page=row.page,
                        tokens=tokens_for_field,
                        bbox=compute_bbox(tokens_for_field),
                        valid=False,
                        reason=None,
                    )

    # Extract Auditor's Opinion
    auditor_kws = flat_config.get("Auditor’s Opinion", [])
    for block in text_blocks:
        text_lower = " ".join(t.text for t in block.tokens).lower()
        for kw in auditor_kws:
            if kw.lower() in text_lower:
                results["Auditor’s Opinion"] = FieldValue(
                    name="Auditor’s Opinion",
                    value=None,
                    raw_text=kw,  # Use the keyword found as standard text
                    page=block.page,
                    tokens=block.tokens,
                    bbox=compute_bbox(block.tokens),
                    valid=True,
                    reason=None,
                )
                break

    return results
