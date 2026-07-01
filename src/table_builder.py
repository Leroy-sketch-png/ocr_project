import re
from collections import defaultdict
from typing import Dict, List, Optional

from .models import TableRow, TextBlock, Token

SECTION_MARKERS = {
    "income_statement": [
        "income statement", "profit and loss", "profit or loss",
        "statement of profit", "statement of comprehensive income",
        "consolidated statement of profit",
    ],
    "balance_sheet": [
        "balance sheet", "statement of financial position",
        "financial position", "consolidated balance",
    ],
    "cash_flow": [
        "cash flow", "statement of cash flows",
    ],
    "changes_in_equity": [
        "changes in equity", "statement of changes",
        "movements in equity",
    ],
    "notes": [
        "notes to the financial",
    ],
}


def detect_page_sections(
    table_rows: List[TableRow],
    text_blocks: List[TextBlock],
) -> Dict[int, str]:
    """
    Scan text_blocks for section-header markers and return {page: section}.

    Uses text_blocks (full line text including headings) for marker detection.
    Uses table_rows only to ensure every page that has financial data gets
    a section assignment (forward-fills from the last seen section header).

    Previous bug: called with (table_rows, text_blocks) but signature only
    accepted one argument — table_rows was silently bound to 'blocks' and
    text_blocks was ignored entirely. Every page resolved to section='unknown'.
    """
    page_section: Dict[int, str] = {}
    current_section = "unknown"

    # Sort text_blocks by page then vertical position
    sorted_blocks = sorted(
        text_blocks,
        key=lambda b: (b.page, b.tokens[0].bbox[1] if b.tokens else 0),
    )

    for block in sorted_blocks:
        line_text = " ".join(t.text for t in block.tokens)

        # Auditor-report trap: prose sentences mention all section names in
        # one long sentence. Real section headers are short (< 80 chars).
        if len(line_text) > 80:
            if block.page not in page_section:
                page_section[block.page] = current_section
            continue

        line_lower = line_text.lower()
        matched = False
        for stype, markers in SECTION_MARKERS.items():
            if any(m in line_lower for m in markers):
                current_section = stype
                matched = True
                break

        # Strict single-word / short-phrase headers
        if not matched:
            stripped = line_lower.strip()
            if stripped in (
                "balance",
                "assets",
                "equity and liabilities",
                "statement of financial statements",
            ):
                current_section = "balance_sheet"
                matched = True

        if block.page not in page_section:
            page_section[block.page] = current_section
        elif matched:
            # A new section header mid-page overrides the earlier assignment
            page_section[block.page] = current_section

    # Forward-fill: ensure every page that has table rows gets a section.
    # Pages beyond the last header page inherit the last known section.
    all_pages = sorted(
        set(r.page for r in table_rows) | set(page_section.keys())
    )
    for page in all_pages:
        if page not in page_section:
            # Find nearest preceding page with a known section
            for prev in reversed([p for p in all_pages if p < page]):
                if prev in page_section:
                    page_section[page] = page_section[prev]
                    break
            else:
                page_section[page] = "unknown"

    return page_section


def build_text_blocks(tokens: List[Token], y_threshold: int = 12) -> List[TextBlock]:
    """Cluster tokens into lines based on vertical proximity."""
    if not tokens:
        return []

    tokens_sorted = sorted(tokens, key=lambda t: (t.page, t.bbox[1]))

    blocks = []
    current_line: List[Token] = [tokens_sorted[0]]

    for i in range(1, len(tokens_sorted)):
        t = tokens_sorted[i]
        prev_t = current_line[-1]

        if t.page == prev_t.page and abs(t.bbox[1] - prev_t.bbox[1]) <= y_threshold:
            current_line.append(t)
        else:
            current_line.sort(key=lambda x: x.bbox[0])
            blocks.append(TextBlock(tokens=current_line, page=prev_t.page))
            current_line = [t]

    if current_line:
        current_line.sort(key=lambda x: x.bbox[0])
        blocks.append(TextBlock(tokens=current_line, page=current_line[0].page))

    return blocks


def is_numeric_token(text: str) -> bool:
    """Check if a token mostly consists of digits, dots, commas, parens, or dashes."""
    if text in ("l", "I", "i", "o", "O"):
        return True
    return bool(re.match(r"^[\d,().\-]+$", text))


def _is_note_reference_row(description: str, cells: List[str]) -> bool:
    """
    Returns True if this row looks like a footnote/note reference row
    rather than a financial data row.
    Signal: single cell, value <= 30, no financial magnitude word in description.
    """
    if len(cells) != 1:
        return False
    val_str = cells[0].replace(",", "").replace(" ", "").strip()
    if not val_str.replace(".", "", 1).isdigit():
        return False
    try:
        if float(val_str) > 30:
            return False
    except ValueError:
        return False
    financial_signals = [
        "total", "net", "gross", "profit", "loss", "assets",
        "liabilities", "equity", "revenue", "capital",
    ]
    if any(sig in description.lower() for sig in financial_signals):
        return False
    return True


def detect_column_boundaries(
    all_groups_on_page: List[List[Token]], gap_threshold: int = 40
) -> List[float]:
    """
    Cluster numeric token groups by right-edge X to find table columns.
    Financial tables are typically right-aligned.
    Returns sorted list of column right-edges.
    """
    if not all_groups_on_page:
        return []
    x_edges = [g[-1].bbox[2] for g in all_groups_on_page]
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
            current_group: List[Token] = []
            current_line_tokens_sorted: List[Token] = []

            for t in tokens:
                if is_numeric_token(t.text):
                    if current_group:
                        prev_t = current_line_tokens_sorted[-1]
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

        for block, numeric_groups, desc_tokens in zip(
            blocks_on_page, block_numeric_groups, block_desc_tokens
        ):
            if not numeric_groups:
                continue

            description = " ".join(t.text for t in desc_tokens).strip()

            cells = [""] * len(col_centers)
            cell_tokens: List[List[Token]] = [[] for _ in range(len(col_centers))]

            for g in numeric_groups:
                g_edge = g[-1].bbox[2]
                if col_centers:
                    closest_col_idx = min(
                        range(len(col_centers)),
                        key=lambda i: abs(col_centers[i] - g_edge),
                    )
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
