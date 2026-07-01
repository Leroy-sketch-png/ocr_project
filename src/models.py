from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Document:
    pages: List[Tuple[int, Any, float]]  # (page_index, PIL.Image, dpi_scale)


@dataclass
class Token:
    text: str
    page: int
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2 in image coords
    confidence: float


@dataclass
class TextBlock:
    tokens: List[Token]
    page: int


@dataclass
class TableRow:
    page: int
    description: str
    cells: List[str]
    cell_tokens: List[List[Token]]


# Confidence levels for extracted field values.
# high     — direct keyword match, no repair applied
# medium   — value was repaired or filled by inverse search (math-anchored)
# low      — inferred from document structure (e.g. zero-NCL rule)
# inferred — derived field with no direct document evidence
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_INFERRED = "inferred"


@dataclass
class FieldValue:
    name: str
    value: Optional[Any]
    raw_text: Optional[str]
    page: Optional[int]
    tokens: List[Token]
    bbox: Optional[Tuple[int, int, int, int]]
    valid: bool
    reason: Optional[str]
    confidence: str = CONFIDENCE_HIGH
    row_candidates: Optional[List[Tuple[str, Optional[float]]]] = None
    field_label: Optional[str] = None
    year: Optional[int] = None
    multi_year: Optional[Dict[int, 'FieldValue']] = None
