import re
from typing import Dict, Optional

from .models import FieldValue


def parse_numeric(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    s = raw.strip()
    if s in ("", "None", "-"):
        return 0.0 if s == "-" else None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]

    # Replace dots and commas that act as thousands separators
    # A dot or comma followed by exactly 3 digits is a thousands separator
    s = re.sub(r"[.,](?=\d{3}(?!\d))", "", s)

    # Remove all spaces (since table_builder separates columns now)
    s = s.replace(" ", "")

    # Strip any trailing commas or dots
    s = s.rstrip(".,")

    # Extract the first float pattern
    match = re.search(r"-?\d+(?:\.\d+)?", s)
    if not match:
        return None

    try:
        val = float(match.group(0))
        return -val if negative else val
    except ValueError:
        return None


def parse_numeric_fields(field_values: Dict[str, FieldValue]) -> None:
    for name, fv in field_values.items():
        if name != "Auditor’s Opinion":
            fv.value = parse_numeric(fv.raw_text)
            if fv.value is None and fv.raw_text:
                fv.valid = False
                fv.reason = "parse_error"
            elif fv.value is not None:
                fv.valid = True
                fv.reason = None
