import re
from typing import Any, Dict, List

from .models import FieldValue, Token


def is_ocr_failure(all_tokens: List[Token]) -> bool:
    if not all_tokens:
        return True

    # Check total confidence
    avg_conf = sum(t.confidence for t in all_tokens) / len(all_tokens)
    if avg_conf < 20.0:
        return True

    # Check number of alphanumeric chars
    total_chars = sum(len(re.sub(r"[^A-Za-z0-9]", "", t.text)) for t in all_tokens)
    if total_chars < 50:
        return True

    return False


def validate_fields(
    field_values: Dict[str, FieldValue], required_fields: List[str]
) -> Dict[str, Any]:
    needs_review = []

    for req in required_fields:
        fv = field_values.get(req)
        if not fv or not fv.valid:
            needs_review.append(req)

    if needs_review:
        return {
            "error": f"Review the following fields: {', '.join(needs_review)}",
            "fields_needing_review": needs_review,
        }
    return {}
