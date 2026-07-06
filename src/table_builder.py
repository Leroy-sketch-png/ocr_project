import re
from collections import defaultdict
from typing import Dict, List, Optional

from .models import TableRow, TextBlock, Token

SECTION_MARKERS = {
    "income_statement": [
        "income statement", "profit and loss", "profit or loss",
        "statement of profit", "statement of comprehensive income",
        "consolidated statement of profit",
        "statements of income", "consolidated statements of income",
        "statement of operations", "statements of operations",
        "statement of earnings",
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
        # Apple uses "CONSOLIDATED STATEMENTS OF SHAREHOLDERS' EQUITY"
        # (U+2019 right single quotation mark in the PDF)
        "statement of shareholders",
        "statements of shareholders",
    ],
    "notes": [
        # IMPORTANT: Do NOT add bare single words like 'notes' here.
        # Any token containing 'notes' (footnote refs, 'notes payable',
        # 'see notes 3', etc.) would permanently freeze the section classifier
        # into the notes state for the rest of the document.
        # Only use specific multi-word phrases that are unambiguous headers.
        "notes to the financial",
        "notes to the consolidated",
        "notes to financial statements",
        "notes to the financial statements",
        # Without 'the' — covers "NOTES TO CONSOLIDATED FINANCIAL STATEMENTS"
        # (Apple, KO) where no article precedes "consolidated".
        "notes to consolidated",
    ],
}

_TOC_LINE_RE = re.compile(r'^.{15,}\s+(\d{1,3})\s*$')


def detect_page_sections(
    table_rows: List[TableRow],
    text_blocks: List[TextBlock],
) -> Dict[int, str]:
    """
    Scan text_blocks for section-header markers and return {page: section}.

    Uses text_blocks (full line text including headings) for marker detection.
    Uses table_rows only to ensure every page that has financial data gets
    a section assignment via forward-fill from the last seen section header.

    Section locking rules:
    - Once 'notes' section is entered it cannot be exited (notes are always
      at the end of a financial report).
    - 'notes' markers are only matched on pages >= 2 (prevents cover page
      or TOC mentions of 'notes' from triggering a premature lock).
    - Lines longer than 50 chars are assumed to be prose/auditor sentences,
      not section headers, and are skipped for marker matching.
    - Lines matching the TOC pattern (text + 2+ spaces + 1-3 digit page
      number) are skipped to prevent table-of-contents pages from triggering
      false section transitions.
    """
    page_section: Dict[int, str] = {}
    current_section = "unknown"

    sorted_blocks = sorted(
        text_blocks,
        key=lambda b: (b.page, b.tokens[0].bbox[1] if b.tokens else 0),
    )

    for block in sorted_blocks:
        line_text = " ".join(t.text for t in block.tokens)

        # Skip prose sentences (auditor reports, director commentary, etc.)
        if len(line_text) > 50:
            if block.page not in page_section:
                page_section[block.page] = current_section
            continue

        # Skip Table of Contents entries
        if _TOC_LINE_RE.match(line_text.strip()):
            if block.page not in page_section:
                page_section[block.page] = current_section
            continue

        line_lower = line_text.lower()
        matched = False
        for stype, markers in SECTION_MARKERS.items():
            if any(m in line_lower for m in markers):
                # Guard 1: notes section only valid from page 2 onward
                if stype == "notes" and block.page < 2:
                    continue
                # Guard 2: once in notes, never leave — notes are always terminal
                if current_section == "notes" and stype != "notes":
                    continue
                current_section = stype
                matched = True
                break

        # Strict single-word / short-phrase balance sheet headers
        # Only applied when NOT already in notes (same lock logic)
        if not matched and current_section != "notes":
            stripped = line_lower.strip()
            if stripped in (
                "balance",
                "assets",
                "equity and liabilities",
                "statement of financial statements",
            ):
                current_section = "balance_sheet"
                matched = True
            elif stripped == "notes" and block.page >= 2:
                current_section = "notes"
                matched = True

        if block.page not in page_section:
            page_section[block.page] = current_section
        elif matched:
            page_section[block.page] = current_section

    # Forward-fill: every page that has table rows but no detected header
    # inherits the section from the nearest preceding page that has one.
    all_pages = sorted(
        set(r.page for r in table_rows) | set(page_section.keys())
    )
    for page in all_pages:
        if page not in page_section:
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
    Returns True if this row looks like a footnote reference row, not data.
    Signal: exactly one cell, value <= 30, no financial keyword in description.
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
    Financial tables are right-aligned. Returns sorted list of column right-edges.
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

    # Post-processing: merge continuation rows whose description starts with a
    # lowercase letter — those are line-wrapped continuations of the prior row
    # (e.g. "Common stock … par value: shares" + "authorized; … respectively").
    # Guard: only merge if the prior description ends with a colon, which is a
    # strong signal that the line wrapped mid-sentence (e.g. "par value: shares"
    # → "authorized; …"). This prevents merging unrelated OCR rows that happen
    # to start with a lowercase word.
    merged = []
    for row in rows:
        if (
            merged
            and row.description
            and row.description[0].islower()
            and row.page == merged[-1].page
            and ":" in merged[-1].description
        ):
            prev = merged[-1]
            prev.description += " " + row.description
            for j, cell in enumerate(row.cells):
                if cell:
                    if prev.cells[j]:
                        prev.cells[j] += " " + cell
                        prev.cell_tokens[j].extend(row.cell_tokens[j])
                    else:
                        prev.cells[j] = cell
                        prev.cell_tokens[j] = list(row.cell_tokens[j])
        else:
            merged.append(row)
    return merged
