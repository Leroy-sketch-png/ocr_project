import re
from typing import List

from .models import TableRow, TextBlock, Token


def build_text_blocks(tokens: List[Token], y_threshold: int = 12) -> List[TextBlock]:
    """
    Cluster tokens into lines based on vertical proximity.
    """
    if not tokens:
        return []

    # Sort by page, then roughly by y (top of bbox)
    tokens_sorted = sorted(tokens, key=lambda t: (t.page, t.bbox[1]))

    blocks = []
    current_line: List[Token] = [tokens_sorted[0]]

    for i in range(1, len(tokens_sorted)):
        t = tokens_sorted[i]
        prev_t = current_line[-1]

        if t.page == prev_t.page and abs(t.bbox[1] - prev_t.bbox[1]) <= y_threshold:
            current_line.append(t)
        else:
            # Finish current line, sort horizontally
            current_line.sort(key=lambda x: x.bbox[0])
            blocks.append(TextBlock(tokens=current_line, page=prev_t.page))
            current_line = [t]

    if current_line:
        current_line.sort(key=lambda x: x.bbox[0])
        blocks.append(TextBlock(tokens=current_line, page=current_line[0].page))

    return blocks


def is_numeric_token(text: str) -> bool:
    """
    Check if a token mostly consists of digits, dots, commas, parens, or dashes.
    """
    return bool(re.match(r"^[\d,().\-]+$", text))


def _is_note_reference_row(description: str, cells: List[str]) -> bool:
    """
    Returns True if this row looks like a footnote/note reference row
    rather than a financial data row.
    General signal: single numeric cell with value <= 30 and no currency
    context in the description.
    """
    if len(cells) != 1:
        return False
        
    val_str = cells[0].replace(",", "").replace(" ", "").strip()
    
    # Check if the string is numeric (including decimals)
    if not val_str.replace(".", "", 1).isdigit():
        return False
        
    try:
        val_float = float(val_str)
        if val_float > 30:
            return False
    except ValueError:
        return False
        
    # If the description has financial magnitude words, it's real data
    financial_signals = ["total", "net", "gross", "profit", "loss", "assets",
                         "liabilities", "equity", "revenue", "capital"]
    desc_lower = description.lower()
    if any(sig in desc_lower for sig in financial_signals):
        return False
    return True


def build_table_rows(text_blocks: List[TextBlock]) -> List[TableRow]:
    """
    Identify lines containing financial data and construct TableRows.
    """
    rows = []
    for block in text_blocks:
        tokens = block.tokens
        desc_tokens = []
        numeric_groups = []
        current_group = []
        current_line_tokens_sorted = []

        for t in tokens:
            if is_numeric_token(t.text):
                if current_group:
                    prev_t = current_line_tokens_sorted[-1]
                    # Check X-gap. With 300 DPI, gaps are 3x larger. Use 24 pixels.
                    gap = t.bbox[0] - prev_t.bbox[2]
                    if gap > 24:
                        numeric_groups.append(current_group)
                        current_group = []
                current_group.append(t)
                current_line_tokens_sorted.append(t)
            else:
                if current_group:
                    numeric_groups.append(current_group)
                    current_group = []
                desc_tokens.append(t)
                current_line_tokens_sorted.append(t)

        if current_group:
            numeric_groups.append(current_group)

        if not numeric_groups:
            continue  # not a financial row

        description = " ".join(t.text for t in desc_tokens).strip()
        cells = []
        cell_tokens = []
        for g in numeric_groups:
            value_text = " ".join(t.text for t in g)
            cells.append(value_text)
            cell_tokens.append(g)

        if _is_note_reference_row(description, cells):
            continue

        rows.append(
            TableRow(
                page=block.page,
                description=description,
                cells=cells,
                cell_tokens=cell_tokens,
            )
        )
    return rows
