import re
from typing import Any, Dict, List, Optional, Tuple

import yaml
from thefuzz import fuzz

from .models import FieldValue, TableRow, TextBlock, Token
from .value_parser import parse_numeric


def compute_match_score(query: str, desc: str, extra_restricting: set = None) -> float:
    """
    Score how well `desc` matches `query`.

    Design:
    - Full token overlap (query tokens ⊆ desc tokens): high score, no extra-word penalty.
    - Partial overlap: scaled score, small extra-word penalty.
    - No overlap: fall back to fuzzy ratio.

    Restricting modifiers (current, noncurrent, fixed, intangible, short-term,
    long-term) that break the semantic meaning of a containment match are
    penalised to prevent "Total Assets" from matching "Total current assets".
    """
    q_clean = re.sub(r"[^a-z0-9\s]", "", query.lower())
    d_clean = re.sub(r"[^a-z0-9\s]", "", desc.lower())

    q_tokens = set(q_clean.split())
    d_tokens = set(d_clean.split())

    # Grammatical stop words — semantically neutral.
    _STOPS = {"and", "or", "the", "of", "for", "in"}
    q_tokens -= _STOPS
    d_tokens -= _STOPS

    if not q_tokens or not d_tokens:
        return fuzz.ratio(q_clean, d_clean)

    intersection = q_tokens.intersection(d_tokens)
    if not intersection:
        return fuzz.ratio(q_clean, d_clean)

    recall = len(intersection) / len(q_tokens)
    extra_desc_tokens = d_tokens - q_tokens

    # Restricting modifiers that change which line item is referenced
    # (e.g., "current" in "Total current assets" ≠ "Total Assets").
    # "equity" and "liabilities" prevent "Total Liabilities" from matching
    # "Total liabilities and equity" (a different line).
    # "loss" prevents "Other income (loss)" from matching "Net Loss" / "Net income".
    _RESTRICTING = {
        "current", "noncurrent", "fixed", "intangible",
        "shortterm", "longterm",
        "equity", "liabilities",
        "loss",
    }
    if extra_restricting:
        _RESTRICTING = _RESTRICTING | extra_restricting

    if recall == 1.0:
        # All query tokens present in description — containment match.
        # Penalise if desc contains restricting modifiers that break the
        # semantic meaning (e.g., "Total current assets" for "Total Assets").
        has_restricting = bool(extra_desc_tokens & _RESTRICTING)
        if has_restricting:
            return 45.0  # below 82 threshold regardless of fuzzy similarity
        base = 95.0
        # Small penalty only if description is very long
        if len(extra_desc_tokens) > len(q_tokens) * 2:
            base -= 5.0
        return max(base, fuzz.ratio(q_clean, d_clean))
    elif recall >= 0.5:
        # Partial overlap — keep a small extra-word penalty
        score = (recall * 90) - (len(extra_desc_tokens) * 5)
        return max(score, fuzz.ratio(q_clean, d_clean))

    return fuzz.ratio(q_clean, d_clean)


def load_field_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        if not isinstance(config, dict):
            return {}
        return config


def compute_bbox(tokens: List[Token]) -> Optional[Tuple[int, int, int, int]]:
    if not tokens:
        return None
    x1 = min(t.bbox[0] for t in tokens)
    y1 = min(t.bbox[1] for t in tokens)
    x2 = max(t.bbox[2] for t in tokens)
    y2 = max(t.bbox[3] for t in tokens)
    return (x1, y1, x2, y2)


def normalize_auditor_opinion(text: str) -> Optional[str]:
    """
    Classify an auditor's opinion block into one of the four standard labels.

    Handles both:
    - Compact phrases: "qualified opinion", "adverse opinion"
    - Split mentions: "our opinion is unmodified" (no adjacent "opinion")
    """
    t = text.lower()

    # Check for disclaimer first (most specific — a disclaimer IS NOT a qualified)
    if "disclaimer of opinion" in t or ("disclaim" in t and "opinion" in t):
        return "Disclaimer"

    # Adverse opinion
    if "adverse" in t and ("opinion" in t or "view" in t):
        return "Adverse"

    # Qualified opinion — "qualified" appears near "opinion"
    # Also catches "except for" which is the standard signal of a qualified opinion
    if "qualified opinion" in t or ("qualified" in t and "opinion" in t):
        # Make sure it's not "unqualified" being matched by "qualified"
        if "unqualified" not in t and "unmodified" not in t:
            return "Qualified"
    if "except for" in t:
        return "Qualified"

    # Unqualified / clean / unmodified opinion
    if (
        "unqualified opinion" in t
        or "unmodified opinion" in t
        or "unmodified" in t
        or "true and fair view" in t
        or "present fairly" in t
        or "clean opinion" in t
    ):
        return "Unqualified"

    return None


def _cross_field_collision_check(results: dict) -> dict:
    """
    If the same value AND same page AND same raw_text are assigned to two different
    fields, the lower-scoring one is a collision and should be nulled out.
    This is general: in a real financial statement, the same table cell cannot
    be both Current Liabilities and Non-Current Liabilities.
    """
    seen = {}  # (page, raw_text) -> field_name
    to_null = []
    for field_name, fv in results.items():
        if fv is None or fv.raw_text is None:
            continue
        key = (fv.page, fv.raw_text)
        if key in seen:
            existing_field = seen[key]
            if "Non-Current" in field_name and "Non-Current" not in existing_field:
                to_null.append(field_name)
            elif "Non-Current" in existing_field and "Non-Current" not in field_name:
                to_null.append(existing_field)
        else:
            seen[key] = field_name
    for f in to_null:
        del results[f]
    return results


def detect_year_column(
    table_rows: List[TableRow],
    page_section_map: Optional[Dict[int, str]] = None,
) -> Tuple[Dict[int, float], Dict[int, Dict[int, float]], Dict[int, float]]:
    """
    Detect the most-recent-year column X position per page.

    Only pages whose section marker is explicitly non-financial ("notes")
    skip primary year detection — they inherit years via forward/backward
    fill.  "Unknown" pages are not skipped because they may be financial
    statements the section detector failed to identify.

    Returns:
      primary_map:    {page: x_coord_of_most_recent_year}
      full_year_map:  {page: {year: x_coord}}
      col_pitch_map:  {page: tightest_inter_column_gap_in_pixels}
                      Used downstream as a DPI-aware cell-selection radius.
                      Pages with only one detected year get pitch=None (caller
                      falls back to 200*dpi_scale).
    """
    page_year_x: Dict[int, float] = {}
    full_year_map: Dict[int, Dict[int, float]] = {}
    col_pitch_map: Dict[int, float] = {}
    last_seen_x = None
    last_seen_full = None
    last_seen_pitch = None

    pages = sorted(list(set(row.page for row in table_rows)))

    # Pages whose section marker is definitely non-financial (notes)
    # should not contribute year detections.  "Unknown" pages are
    # likely financial statements the section detector missed.
    _SKIP_SECTIONS = {"notes"}

    for page in pages:
        skip_page = (
            page_section_map is not None
            and page_section_map.get(page) in _SKIP_SECTIONS
        )

        page_rows = [r for r in table_rows if r.page == page]

        # Aggregate year cells in the top 10 rows.
        year_cells = []
        if not skip_page:
            for row in page_rows[:10]:
                for cell_text, tokens in zip(row.cells, row.cell_tokens):
                    # Accept a cell as a year header if the text, after
                    # stripping leading/trailing whitespace and trailing
                    # punctuation (commas, periods, brackets), is a pure
                    # digit string in 1990–2030.  Financial values with
                    # thousands-separator commas (e.g. "2,009") will still
                    # have the internal comma after rstrip → isdigit() is
                    # False → correctly rejected.
                    stripped = cell_text.strip().rstrip(",.)}]")
                    if stripped.isdigit() and 1990 <= int(stripped) <= 2030 and tokens:
                        min_x = min(t.bbox[0] for t in tokens)
                        max_x = max(t.bbox[2] for t in tokens)
                        center_x = (min_x + max_x) / 2.0
                        year_cells.append((center_x, int(stripped)))

        unique_years = {y[1]: y[0] for y in year_cells}

        # Deduplicate by X coordinate: if multiple year-like values share the
        # same column X (e.g. a "2007" data value aligned under the "2024"
        # header), keep only the most recent year for that column.
        # Tolerance of 20px accounts for header-centering vs data-right-alignment.
        _X_TOLERANCE = 20.0
        x_yr = {}  # center_x → best_year
        for yr, cx in unique_years.items():
            match_x = next((ex for ex in x_yr if abs(cx - ex) < _X_TOLERANCE), None)
            if match_x is not None:
                if yr > x_yr[match_x]:
                    x_yr[match_x] = yr
            else:
                x_yr[cx] = yr
        unique_years_rebuilt = {yr: cx for cx, yr in x_yr.items()}

        if len(unique_years_rebuilt) >= 2:
            target_x = unique_years_rebuilt[max(unique_years_rebuilt.keys())]
            page_year_x[page] = target_x
            full_year_map[page] = dict(unique_years_rebuilt)
            last_seen_x = target_x
            last_seen_full = dict(unique_years_rebuilt)

            # Compute tightest gap between adjacent year column centres.
            xs = sorted(unique_years_rebuilt.values())
            gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
            pitch = min(gaps) if gaps else None
            col_pitch_map[page] = pitch
            last_seen_pitch = pitch

        if page not in page_year_x and last_seen_x is not None:
            page_year_x[page] = last_seen_x
            full_year_map[page] = last_seen_full
            col_pitch_map[page] = last_seen_pitch

    # Backward-fill pages that are still missing
    for page in reversed(pages):
        if page not in page_year_x:
            for future_page in sorted([p for p in page_year_x if p > page]):
                page_year_x[page] = page_year_x[future_page]
                full_year_map[page] = full_year_map.get(future_page, {})
                col_pitch_map[page] = col_pitch_map.get(future_page)
                break

    return page_year_x, full_year_map, col_pitch_map


def extract_fields(
    table_rows: List[TableRow],
    text_blocks: List[TextBlock],
    config: Dict[str, Any],
    year_x_map: Dict[int, float] = None,
    full_year_map: Dict[int, Dict[int, float]] = None,
    page_section_map: Dict[int, str] = None,
    dpi_scale: float = 1.0,
    col_pitch_map: Dict[int, float] = None,
) -> Dict[str, FieldValue]:
    results = {}
    flat_config = {}

    for section, fields in config.items():
        if not isinstance(fields, dict):
            continue
        for field_name, details in fields.items():
            if field_name.startswith("_"):
                continue
            if isinstance(details, dict):
                flat_config[field_name] = {"keywords": details.get("keywords", []), **details}
            else:
                flat_config[field_name] = {"keywords": details}
            if "_section" in fields:
                flat_config[field_name]["_section"] = fields["_section"]

    # Track the highest scoring row per field
    best_scores = {k: -1.0 for k in flat_config.keys()}

    def _col_threshold(page: int) -> float:
        """Return cell-selection distance threshold for this page.

        Uses 55% of the detected inter-column pitch so we stay firmly within
        the target year column and never bleed into adjacent year columns.
        Falls back to 200*dpi_scale for single-year or un-detected layouts.
        """
        pitch = (col_pitch_map or {}).get(page)
        if pitch and pitch > 0:
            return 0.55 * pitch
        return 200.0 * dpi_scale

    for row in table_rows:
        desc_lower = row.description.lower()
        for field_name, field_cfg in flat_config.items():
            if field_name == "Auditor's Opinion":
                continue  # Handled separately

            field_section = field_cfg.get("_section")
            if page_section_map and field_section:
                row_section = page_section_map.get(row.page, "unknown")
                if row_section != "unknown" and row_section != field_section:
                    continue  # confirmed section mismatch — skip

            kws = field_cfg.get("keywords", [])
            extra_restr = field_cfg.get("_restricting_extra")
            extra_restr_set = set(extra_restr) if isinstance(extra_restr, list) else None
            best_kw_score = max(
                (compute_match_score(kw, desc_lower, extra_restricting=extra_restr_set) for kw in kws), default=0.0
            )

            if best_kw_score >= 82:
                best_cell_idx = None
                target_x = year_x_map.get(row.page) if year_x_map else None

                if target_x is not None:
                    # Find the parseable cell whose centre is closest to target_x
                    # but within the dynamic column-pitch radius.
                    threshold = _col_threshold(row.page)
                    closest_idx = None
                    min_dist = float("inf")
                    for i, tokens in enumerate(row.cell_tokens):
                        if not tokens:
                            continue
                        if parse_numeric(row.cells[i]) is None:
                            continue
                        min_cx = min(t.bbox[0] for t in tokens)
                        max_cx = max(t.bbox[2] for t in tokens)
                        center_x = (min_cx + max_cx) / 2.0
                        dist = abs(center_x - target_x)
                        if dist < min_dist:
                            min_dist = dist
                            closest_idx = i

                    if closest_idx is not None and min_dist < threshold:
                        best_cell_idx = closest_idx

                if best_cell_idx is None and target_x is None:
                    # No year-column detected — pick the cell with the largest
                    # absolute value (most likely the primary financial figure).
                    best_abs = -1.0
                    for idx in range(len(row.cells)):
                        v = parse_numeric(row.cells[idx])
                        if v is not None and abs(v) > best_abs:
                            best_abs = abs(v)
                            best_cell_idx = idx
                    if best_cell_idx is None:
                        best_cell_idx = 0

                raw_text = row.cells[best_cell_idx] if (row.cells and best_cell_idx is not None) else None
                val = parse_numeric(raw_text)

                # Reject sectioned-field matches on unknown pages when no valid cell
                # was found within the year-column radius (value would be None, and
                # the match is almost certainly a prose word, not a financial data row).
                if field_section and page_section_map:
                    row_section = page_section_map.get(row.page, "unknown")
                    if row_section == "unknown" and val is None:
                        continue

                field_cfg = flat_config.get(field_name, {})
                if field_cfg.get("sign") == "negative" and val is not None and val > 0:
                    val = -val
                    if raw_text and not raw_text.startswith("-") and "(" not in raw_text:
                        raw_text = "-" + raw_text

                current_best_score = best_scores[field_name]
                should_update = False

                # Determine section-match status for new and existing candidates
                current_row_section = page_section_map.get(row.page, "unknown") if page_section_map else "unknown"
                new_section_matches = (field_section and current_row_section != "unknown" and current_row_section == field_section)

                existing_section_matches = False
                existing_val = None
                existing_page = None
                if field_name in results and results[field_name] is not None:
                    existing_val = parse_numeric(results[field_name].raw_text)
                    existing_page = results[field_name].page
                    existing_row_section = page_section_map.get(existing_page, "unknown") if page_section_map else "unknown"
                    existing_section_matches = (field_section and existing_row_section != "unknown" and existing_row_section == field_section)

                # Apply section bonus to effective score
                NEW_SECTION_BONUS = 10
                new_effective = best_kw_score + (NEW_SECTION_BONUS if new_section_matches else 0)
                existing_effective = current_best_score + (NEW_SECTION_BONUS if existing_section_matches else 0)

                if (
                    val is not None
                    and abs(val) < 1e-6
                    and existing_val is not None
                    and abs(existing_val) > 1e-6
                ):
                    # New candidate is zero but existing is non-zero:
                    # add as candidate only, do not replace primary value.
                    if field_name in results and results[field_name] is not None:
                        results[field_name].row_candidates.append((raw_text, val))
                    should_update = False
                elif new_effective > existing_effective:
                    should_update = True
                elif new_effective == existing_effective:
                    should_update = False
                    if val is not None and existing_val is not None:
                        # Tiebreaker: prefer section-matched; then earlier page; then larger value
                        if new_section_matches and not existing_section_matches:
                            should_update = True
                        elif existing_section_matches and not new_section_matches:
                            should_update = False
                        elif row.page < existing_page:
                            should_update = True
                        elif row.page == existing_page:
                            should_update = False
                        elif abs(val) > abs(existing_val) * 10:
                            should_update = True
                    elif val is not None and existing_val is None:
                        should_update = True
                    elif existing_val is not None and val is None:
                        should_update = False

                    if field_name in results and not should_update:
                        for cell_text in row.cells:
                            cval = parse_numeric(cell_text)
                            if cval is not None:
                                field_cfg = flat_config.get(field_name, {})
                                if field_cfg.get("sign") == "negative" and cval > 0:
                                    cval = -cval
                                    if cell_text and not cell_text.startswith("-") and "(" not in cell_text:
                                        cell_text = "-" + cell_text
                                results[field_name].row_candidates.append((cell_text, cval))
                else:
                    # Score worse — collect as candidate for multi-year inference
                    if field_name in results:
                        for cell_text in row.cells:
                            cval = parse_numeric(cell_text)
                            if cval is not None:
                                field_cfg = flat_config.get(field_name, {})
                                if field_cfg.get("sign") == "negative" and cval > 0:
                                    cval = -cval
                                    if cell_text and not cell_text.startswith("-") and "(" not in cell_text:
                                        cell_text = "-" + cell_text
                                results[field_name].row_candidates.append((cell_text, cval))

                if should_update:
                    best_scores[field_name] = best_kw_score
                    tokens_for_field = (
                        row.cell_tokens[best_cell_idx]
                        if (row.cell_tokens and best_cell_idx is not None)
                        else []
                    )

                    row_cands = []
                    if field_name in results and results[field_name] is not None:
                        old_fv = results[field_name]
                        if old_fv.row_candidates:
                            row_cands.extend(old_fv.row_candidates)
                        old_val = parse_numeric(old_fv.raw_text)
                        if old_val is not None:
                            if not any(c == old_val for _, c in row_cands):
                                row_cands.append((old_fv.raw_text, old_val))

                    for cell_text in row.cells:
                        cval = parse_numeric(cell_text)
                        if cval is not None:
                            if not any(c == cval for _, c in row_cands):
                                row_cands.append((cell_text, cval))

                    results[field_name] = FieldValue(
                        name=field_name,
                        value=None,  # parsed by repair engine
                        raw_text=raw_text,
                        page=row.page,
                        tokens=tokens_for_field,
                        bbox=compute_bbox(tokens_for_field),
                        valid=False,
                        reason=None,
                        row_candidates=row_cands,
                        field_label=row.description,
                    )

    def _clear_field_value(fv):
        """Clear a field's extracted data so it can be recomputed from equation.
        Preserves page for equation computation. Resets field_label to canonical
        name so multi-year row matching doesn't re-find the wrong row."""
        if fv is None:
            return
        fv.value = None
        fv.raw_text = None
        fv.tokens = []
        fv.bbox = None
        fv.valid = False
        fv.reason = None
        fv.row_candidates = []
        fv.multi_year = None
        fv.field_label = fv.name

    # Resolve bbox conflicts: if multiple fields claim the same bbox,
    # keep only the highest-scoring match. The loser's value is cleared
    # (set to None) so it can be recomputed from its accounting equation
    # via the inverse search or multi-year propagation in main.py.
    bbox_owners = {}  # tuple(bbox, page) -> (field_name, score)
    for fn in list(results.keys()):
        fv = results[fn]
        if fv is None or fv.bbox is None:
            continue
        key = (tuple(fv.bbox), fv.page)
        if key in bbox_owners:
            existing_fn, existing_score = bbox_owners[key]
            current_score = best_scores.get(fn, 0)
            if current_score > existing_score:
                _clear_field_value(results[existing_fn])
                bbox_owners[key] = (fn, current_score)
            else:
                _clear_field_value(results[fn])
        else:
            bbox_owners[key] = (fn, best_scores.get(fn, 0))

    # -----------------------------------------------------------------------
    # Auditor's Opinion — handled via full text-block scan, not table rows
    # -----------------------------------------------------------------------
    auditor_config = flat_config.get("Auditor's Opinion", {})
    auditor_kws = auditor_config.get("keywords", []) if isinstance(auditor_config, dict) else auditor_config
    found_opinion = False
    for block in text_blocks:
        if found_opinion:
            break
        text_lower = " ".join(t.text for t in block.tokens).lower()
        for kw in auditor_kws:
            if kw.lower() in text_lower:
                opinion_value = normalize_auditor_opinion(text_lower)
                if opinion_value is None:
                    continue
                first_line = []
                for tok in block.tokens:
                    if first_line and tok.bbox[0] - first_line[-1].bbox[2] > 100:
                        break
                    if len(first_line) >= 8:
                        break
                    first_line.append(tok)
                field_label_text = " ".join(t.text for t in first_line) if first_line else kw
                results["Auditor's Opinion"] = FieldValue(
                    name="Auditor's Opinion",
                    value=opinion_value,
                    raw_text=kw,
                    page=block.page,
                    tokens=block.tokens,
                    bbox=compute_bbox(block.tokens),
                    valid=True,
                    reason=None,
                    field_label=field_label_text,
                )
                found_opinion = True
                break

    if not found_opinion:
        for block in text_blocks:
            text_lower = " ".join(t.text for t in block.tokens).lower()
            opinion_value = normalize_auditor_opinion(text_lower)
            if opinion_value is not None:
                first_line = []
                for tok in block.tokens:
                    if first_line and tok.bbox[0] - first_line[-1].bbox[2] > 100:
                        break
                    if len(first_line) >= 8:
                        break
                    first_line.append(tok)
                field_label_text = " ".join(t.text for t in first_line) if first_line else opinion_value
                results["Auditor's Opinion"] = FieldValue(
                    name="Auditor's Opinion",
                    value=opinion_value,
                    raw_text=text_lower[:80],
                    page=block.page,
                    tokens=block.tokens,
                    bbox=compute_bbox(block.tokens),
                    valid=True,
                    reason="full_text_fallback",
                    field_label=field_label_text,
                )
                found_opinion = True
                break

    results = _cross_field_collision_check(results)

    # -----------------------------------------------------------------------
    # Multi-year extraction — populate FieldValue.multi_year per field
    # -----------------------------------------------------------------------
    multi_year_results = {}
    for field_name, primary_fv in results.items():
        if primary_fv is None or primary_fv.page is None or field_name == "Auditor's Opinion":
            continue
        years_on_page = full_year_map.get(primary_fv.page, {}) if full_year_map else {}
        if len(years_on_page) < 2:
            primary_year = max(years_on_page.keys()) if years_on_page else None
            multi_year_results[field_name] = (
                {primary_year: primary_fv} if primary_year else {"primary": primary_fv}
            )
            continue

        matched_row = None
        for row in table_rows:
            if row.page == primary_fv.page and row.description == primary_fv.field_label:
                matched_row = row
                break

        if matched_row is None:
            if primary_fv.value is not None:
                primary_year = max(years_on_page.keys())
                multi_year_results[field_name] = {primary_year: primary_fv}
            continue

        year_values = {}
        for yr, yr_x in sorted(years_on_page.items()):
            threshold = _col_threshold(matched_row.page)
            best_cell_idx = None
            min_dist = float("inf")
            for i, tokens in enumerate(matched_row.cell_tokens):
                if not tokens:
                    continue
                if parse_numeric(matched_row.cells[i]) is None:
                    continue
                min_cx = min(t.bbox[0] for t in tokens)
                max_cx = max(t.bbox[2] for t in tokens)
                center_x = (min_cx + max_cx) / 2.0
                dist = abs(center_x - yr_x)
                if dist < min_dist and dist < threshold:
                    min_dist = dist
                    best_cell_idx = i

            if best_cell_idx is None:
                continue

            raw = matched_row.cells[best_cell_idx]
            val = parse_numeric(raw)
            field_cfg = flat_config.get(field_name, {})
            if field_cfg.get("sign") == "negative" and val is not None and val > 0:
                val = -val
                if raw and not raw.startswith("-") and "(" not in raw:
                    raw = "-" + raw

            year_fv = FieldValue(
                name=field_name,
                value=val,
                raw_text=raw,
                page=matched_row.page,
                tokens=matched_row.cell_tokens[best_cell_idx],
                bbox=compute_bbox(matched_row.cell_tokens[best_cell_idx]),
                valid=True,
                reason=None,
                row_candidates=[],
                field_label=field_name,
                year=yr,
            )
            year_values[yr] = year_fv

        multi_year_results[field_name] = year_values if year_values else {None: primary_fv}

    for field_name, yr_map in multi_year_results.items():
        if field_name in results and results[field_name] is not None:
            results[field_name].multi_year = yr_map

    # Seed inferred fields that had no primary match so the accounting equation
    # in main.py can still compute them via multi-year propagation.
    from .models import FieldValue as _FieldValue
    for field_name, field_cfg in flat_config.items():
        if field_name not in results and field_cfg.get("inferred"):
            results[field_name] = _FieldValue(
                name=field_name,
                value=None,
                raw_text=None,
                page=None,
                tokens=[],
                bbox=None,
                valid=False,
                reason="Inferred field — no primary OCR match",
                row_candidates=[],
                field_label=field_name,
            )

    return results
