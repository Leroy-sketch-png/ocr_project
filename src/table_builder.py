import re
from typing import List

from .models import TableRow, TextBlock, Token


def build_text_blocks(tokens: List[Token], y_threshold: int = 15) -> List[TextBlock]:
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

        for t in tokens:
            if is_numeric_token(t.text):
                current_group.append(t)
            else:
                if current_group:
                    numeric_groups.append(current_group)
                    current_group = []
                desc_tokens.append(t)
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

        rows.append(
            TableRow(
                page=block.page,
                description=description,
                cells=cells,
                cell_tokens=cell_tokens,
            )
        )
    return rows
