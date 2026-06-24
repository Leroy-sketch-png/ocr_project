from typing import Any, Dict

from .models import FieldValue


def field_value_to_dict(f: FieldValue) -> Dict[str, Any]:
    if f is None:
        return {
            "value": None,
            "evidence": None,
            "page": None,
        }

    if f.tokens:
        evidence_text = " ".join(
            t.text
            for t in sorted(f.tokens, key=lambda tk: (tk.page, tk.bbox[1], tk.bbox[0]))
        )
        page = f.tokens[0].page
    else:
        evidence_text = f.raw_text or ""
        page = f.page or 0

    return {
        "value": f.value,
        "raw_text": f.raw_text,
        "evidence": evidence_text,
        "page": page,
        "bbox": f.bbox,
        "valid": f.valid,
        "reason": f.reason,
    }
