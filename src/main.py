import argparse
import json
import logging
import sys
from pathlib import Path

from .exporter import export_fields
from .field_extractor import detect_year_column, extract_fields, load_field_config
from .image_processor import preprocess_image
from .io_loader import load_document
from .models import CONFIDENCE_HIGH
from .ocr_engine import get_ocr_engine
from .repair_engine import apply_math_repairs
from .table_builder import build_table_rows, build_text_blocks, detect_page_sections
from .text_extractor import extract_tokens_from_pdf, has_extractable_text
from .validator import validate_fields
from .value_parser import parse_numeric

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).parent / "field_config.yaml"


def process_file(
    pdf_path: str,
    config_path: str = str(_DEFAULT_CONFIG),
    optimization_mode: bool = False,
) -> dict:
    """Process a single PDF/image file and return extracted fields."""
    config = load_field_config(config_path)

    all_tokens = []
    processed_images = {}
    dpi_scale = 1.0

    # Fast path: text-based PDF → extract tokens via pdfplumber (no OCR).
    # Falls back to Tesseract OCR for scanned/image-based PDFs.
    if has_extractable_text(pdf_path):
        all_tokens = extract_tokens_from_pdf(pdf_path)
        # dpi_scale = 1.0: coordinates already scaled to 300 DPI pixel space
        # in text_extractor.py via _PDF_PT_TO_PX multiplier
    else:
        doc = load_document(pdf_path)
        engine = get_ocr_engine("tesseract")

        for page_idx, (page_num, page_image, page_dpi_scale) in enumerate(doc.pages):
            processed = preprocess_image(page_image)
            processed_images[page_num] = processed
            tokens = engine.recognize_page(processed, page_num)
            all_tokens.extend(tokens)
            if page_num == 1:
                dpi_scale = page_dpi_scale

    text_blocks = build_text_blocks(all_tokens)
    table_rows = build_table_rows(text_blocks, dpi_scale=dpi_scale)

    page_section_map = detect_page_sections(table_rows, text_blocks)

    # detect_year_column now returns three values
    year_x_map, full_year_map, col_pitch_map = detect_year_column(
        table_rows, page_section_map=page_section_map
    )

    fields = extract_fields(
        table_rows,
        text_blocks,
        config,
        year_x_map=year_x_map,
        full_year_map=full_year_map,
        page_section_map=page_section_map,
        dpi_scale=dpi_scale,
        col_pitch_map=col_pitch_map,
    )

    # Build flat_config for structural NCL guard in repair engine
    flat_config = {}
    for section, section_fields in config.items():
        if not isinstance(section_fields, dict):
            continue
        for field_name, details in section_fields.items():
            if field_name.startswith("_"):
                continue
            if isinstance(details, dict):
                flat_config[field_name] = {"keywords": details.get("keywords", []), **details}
            else:
                flat_config[field_name] = {"keywords": details}

    # Parse raw_text -> value on every extracted field
    for fv in fields.values():
        if fv is not None and fv.value is None and fv.raw_text is not None:
            fv.value = parse_numeric(fv.raw_text)
            if fv.value is not None:
                fv.valid = True
                fv.confidence = CONFIDENCE_HIGH

    repaired = apply_math_repairs(
        fields,
        processed_images,
        all_tokens=all_tokens if optimization_mode else None,
        optimization_mode=optimization_mode,
        dpi_scale=dpi_scale,
        page_section_map=page_section_map,
        table_rows=table_rows,
        flat_config=flat_config,
    )

    # The repair engine replaces some FieldValues via _make_field_value,
    # which drops multi_year. Restore it from the pre-repair fields dict.
    for field_name in repaired:
        fv = repaired[field_name]
        if fv is not None and fv.multi_year is None and field_name in fields:
            old = fields[field_name]
            if old is not None and old.multi_year is not None:
                fv.multi_year = old.multi_year

    # Inverse-search-inferred fields get a new primary value but may retain
    # stale multi_year from pre-repair. Clear it so the equation computation
    # below can build correct multi_year for all years (e.g. ITE from PBT - NP).
    for fv in repaired.values():
        if fv is not None and fv.multi_year is not None:
            reason = getattr(fv, "reason", None) or ""
            if "inverse_search" in reason:
                fv.multi_year = None

    # For inferred fields that still lack multi_year, compute it from
    # their accounting equation if all summands have year data.
    from .repair_engine import EQUATIONS
    from copy import deepcopy

    def _compute_multi_year(target, summands, repaired, field_to_compute):
        """Populate multi_year for field_to_compute using equation."""
        fv = repaired.get(field_to_compute)
        if fv is None:
            return
        if fv.multi_year is not None:
            # For inferred fields (e.g. Other Reserves), always recompute
            # from equation; OCR values may be noisy.
            fc = flat_config.get(field_to_compute, {})
            if not fc.get("inferred"):
                return
        # Gather all equation participants and their multi_year
        all_participants = [target] + summands
        source_years = {}
        for p in all_participants:
            pfv = repaired.get(p)
            if pfv is not None and pfv.multi_year is not None:
                source_years[p] = {y: pfv.multi_year[y].value for y in pfv.multi_year if y is not None}
        # Need all participants except the one being computed
        needed = {p for p in all_participants if p != field_to_compute}
        if not needed.issubset(source_years.keys()):
            return
        common_years = set.intersection(*[set(source_years[p].keys()) for p in needed]) if needed else set()
        if not common_years:
            return
        yr_map = {}
        for yr in sorted(common_years):
            other_sum = sum(source_years[p].get(yr, 0) for p in needed)
            if field_to_compute == target:
                yr_val = other_sum
            else:
                yr_val = source_years[target][yr] - sum(source_years[p].get(yr, 0) for p in needed if p != target)
            yr_fv = deepcopy(fv)
            yr_fv.value = yr_val
            yr_fv.year = yr
            yr_fv.multi_year = None
            yr_fv.raw_text = None
            yr_fv.tokens = []
            yr_fv.bbox = None
            yr_map[yr] = yr_fv
        fv.multi_year = yr_map

    for target, summands in EQUATIONS:
        _compute_multi_year(target, summands, repaired, target)
        for s in summands:
            _compute_multi_year(target, summands, repaired, s)

    # Propagate repaired primary values into multi_year entries so the
    # years dict reflects corrections (e.g., PBT = NP + ITE repairs).
    for fv in repaired.values():
        if fv is None or not fv.multi_year or fv.page is None:
            continue
        yr_keys = [k for k in fv.multi_year if k is not None]
        if not yr_keys:
            continue
        max_yr = max(yr_keys)
        my_fv = fv.multi_year[max_yr]
        if my_fv is not None and fv.value is not None and my_fv.value != fv.value:
            my_fv.value = fv.value
            my_fv.raw_text = fv.raw_text
            my_fv.tokens = fv.tokens
            my_fv.bbox = fv.bbox
            my_fv.confidence = fv.confidence

    validated = validate_fields(repaired)

    return export_fields(validated)


def main():
    parser = argparse.ArgumentParser(description="OCR financial document extractor")
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG), help="Field config YAML")
    parser.add_argument("--output", help="Write JSON output to this file instead of stdout")
    parser.add_argument("--optimize", action="store_true", default=True, help="Enable optimization mode")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, force=True)
    else:
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)

    result = process_file(args.pdf, args.config, optimization_mode=args.optimize)

    output_json = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
