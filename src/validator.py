import logging
import re
from typing import Any, Dict, List, Optional

from .models import CONFIDENCE_INFERRED, CONFIDENCE_LOW, FieldValue, Token

logger = logging.getLogger(__name__)


def is_ocr_failure(all_tokens: List[Token]) -> bool:
    """Return True if the OCR output looks like a complete recognition failure."""
    if not all_tokens:
        return True
    avg_conf = sum(t.confidence for t in all_tokens) / len(all_tokens)
    if avg_conf < 20.0:
        return True
    total_chars = sum(len(re.sub(r"[^A-Za-z0-9]", "", t.text)) for t in all_tokens)
    if total_chars < 50:
        return True
    return False


def validate_fields(
    field_values: Dict[str, FieldValue],
    required_fields: Optional[List[str]] = None,
) -> Dict[str, FieldValue]:
    """
    Validate extracted fields and flag anything that needs review.

    Changes from the original:
    - required_fields is now Optional (default None / empty list) so
      main.py can call validate_fields(repaired) with one argument.
      Previously the second argument was mandatory, causing a TypeError.
    - Returns the field_values dict unchanged (with .valid flags already
      set by the repair engine). Any field whose value is None is marked
      invalid so the exporter surfaces it correctly.
    - If required_fields is provided, logs a WARNING for any missing field.
    """
    if required_fields is None:
        required_fields = []

    # Mark fields with None value as invalid
    for field_name, fv in field_values.items():
        if fv is not None and fv.value is None:
            fv.valid = False

    # Flag inferred / low-confidence fields in debug log
    for field_name, fv in field_values.items():
        if fv is None:
            continue
        conf = getattr(fv, "confidence", None)
        if conf in (CONFIDENCE_INFERRED, CONFIDENCE_LOW):
            logger.debug(
                "[VALIDATE] %s confidence=%s reason=%s — value is derived/uncertain",
                field_name, conf, fv.reason,
            )

    # Check required fields
    for req in required_fields:
        fv = field_values.get(req)
        if fv is None or fv.value is None:
            logger.warning(
                "[VALIDATE] Required field '%s' is missing from output.", req
            )

    return field_values
