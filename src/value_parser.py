import re
from typing import Dict, Optional

from .models import FieldValue


def parse_numeric(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None

    s = raw.strip()
    if s in ("", "None"):
        return None

    if s == "-":
        return 0.0

    s = s.replace(" ", "")
    negative = False

    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]

    s = s.replace(",", "")

    s_clean = re.sub(r"[^0-9.\-]", "", s)
    if s_clean in ("", "-", "."):
        return None

    try:
        val = float(s_clean)
    except ValueError:
        return None

    if negative and val > 0:
        val = -val

    return val


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
