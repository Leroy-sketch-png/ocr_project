from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple


@dataclass
class Document:
    pages: List[Tuple[int, Any]]  # List of (page_index, PIL.Image)


@dataclass
class Token:
    text: str
    page: int
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2 in image coords
    confidence: float


@dataclass
class TextBlock:
    tokens: List[Token]  # contiguous tokens (e.g., a line)
    page: int


@dataclass
class TableRow:
    page: int
    description: str  # label text (e.g. "Revenue")
    cells: List[str]  # numeric or text values per column
    cell_tokens: List[List[Token]]  # evidence tokens for each cell


@dataclass
class FieldValue:
    name: str
    value: Optional[Any]  # parsed numeric value, normalized category, or None
    raw_text: Optional[str]  # string as seen in document
    page: Optional[int]
    tokens: List[Token]  # evidence
    bbox: Optional[Tuple[int, int, int, int]]
    valid: bool
    reason: Optional[str]  # why invalid / missing
    row_candidates: Optional[List[Tuple[str, Optional[float]]]] = None
    # The exact label string as it appears in the source document.
    # Populated by the field extractor from the matched table row description.
    field_label: Optional[str] = None
