from typing import Any, Dict

from .models import FieldValue


def field_value_to_dict(f: FieldValue) -> Dict[str, Any]:
    if f is None:
        return {
            "field_label": None,
            "value": None,
            "confidence": None,
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

    base = {
        "field_label": f.field_label,
        "value": f.value,
        "confidence": getattr(f, "confidence", "high"),
        "raw_text": f.raw_text,
        "evidence": evidence_text,
        "page": page,
        "bbox": f.bbox,
        "valid": f.valid,
        "reason": f.reason,
        "year": getattr(f, "year", None),
    }

    if getattr(f, "multi_year", None):
        base["years"] = {
            str(yr): {
                "value": yfv.value,
                "raw_text": yfv.raw_text,
                "confidence": getattr(yfv, "confidence", "high"),
                "page": yfv.page,
                "bbox": list(yfv.bbox) if yfv.bbox else None,
            }
            for yr, yfv in f.multi_year.items()
        }
    return base


def export_fields(field_values: Dict[str, FieldValue]) -> Dict[str, Any]:
    """
    Convert the full {field_name: FieldValue} dict produced by the repair
    and validation pipeline into a JSON-serialisable flat dict.

    This is the function called by main.py. It was previously missing,
    causing a NameError on every pipeline run.
    """
    result: Dict[str, Any] = {}
    for field_name, fv in field_values.items():
        result[field_name] = field_value_to_dict(fv)
    return result
