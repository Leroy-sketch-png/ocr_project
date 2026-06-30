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


def detect_column_boundaries(all_groups_on_page: List[List[Token]], gap_threshold: int = 40) -> List[float]:
    """
    Cluster numeric token groups by Right-Edge (X2) to find table columns.
    Financial tables are typically right-aligned.
    Returns sorted list of column Right-edges.
    """
    if not all_groups_on_page:
        return []
        
    x_edges = []
    for g in all_groups_on_page:
        x_edges.append(g[-1].bbox[2])
        
    x_edges.sort()
    
    clusters = []
    current_cluster = [x_edges[0]]
    
    for x in x_edges[1:]:
        if x - current_cluster[-1] <= gap_threshold:
            current_cluster.append(x)
        else:
            clusters.append(sum(current_cluster) / len(current_cluster))
            current_cluster = [x]
            
    if current_cluster:
        clusters.append(sum(current_cluster) / len(current_cluster))
        
    return clusters


def build_table_rows(text_blocks: List[TextBlock], dpi_scale: float = 1.0) -> List[TableRow]:
    """
    Identify lines containing financial data and construct TableRows.
    Aligns cells into unified columns per page using spatial clustering.
    """
    from collections import defaultdict
    page_blocks = defaultdict(list)
    for block in text_blocks:
        page_blocks[block.page].append(block)

    rows = []
    for page, blocks_on_page in page_blocks.items():
        page_numeric_groups = []
        block_numeric_groups = []
        block_desc_tokens = []

        for block in blocks_on_page:
            tokens = block.tokens
            desc_tokens = []
            numeric_groups = []
            current_group = []
            current_line_tokens_sorted = []

            for t in tokens:
                if is_numeric_token(t.text):
                    if current_group:
                        prev_t = current_line_tokens_sorted[-1]
                        # Check X-gap. With 300 DPI, spaces can be 20-40px.
                        # Column gaps are typically >150px.
                        # Use 80 pixels to bridge thousands separators (e.g. '75 427 091')
                        # but still split distinct columns.
                        gap = t.bbox[0] - prev_t.bbox[2]
                        if gap > int(80 * dpi_scale):
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

            page_numeric_groups.extend(numeric_groups)
            block_numeric_groups.append(numeric_groups)
            block_desc_tokens.append(desc_tokens)

        col_centers = detect_column_boundaries(page_numeric_groups, int(40 * dpi_scale))

        for block, numeric_groups, desc_tokens in zip(blocks_on_page, block_numeric_groups, block_desc_tokens):
            if not numeric_groups:
                continue

            description = " ".join(t.text for t in desc_tokens).strip()
            
            cells = [""] * len(col_centers)
            cell_tokens = [[] for _ in range(len(col_centers))]
            
            for g in numeric_groups:
                g_edge = g[-1].bbox[2]
                if col_centers:
                    closest_col_idx = min(range(len(col_centers)), key=lambda i: abs(col_centers[i] - g_edge))
                    # Prevent overwriting if multiple groups map to same column
                    if cells[closest_col_idx]:
                        cells[closest_col_idx] += " " + " ".join(t.text for t in g)
                        cell_tokens[closest_col_idx].extend(g)
                    else:
                        cells[closest_col_idx] = " ".join(t.text for t in g)
                        cell_tokens[closest_col_idx] = list(g)

            non_empty_cells = [c for c in cells if c]
            if _is_note_reference_row(description, non_empty_cells):
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
