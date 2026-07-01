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

    doc = load_document(pdf_path)
    engine = get_ocr_engine("tesseract")

    all_tokens = []
    processed_images = {}
    dpi_scale = 1.0

    # Cache preprocessed page images once per page to avoid double-processing
    for page_idx, (page_num, page_image, page_dpi_scale) in enumerate(doc.pages):
        processed = preprocess_image(page_image)
        processed_images[page_idx] = processed
        tokens = engine.recognize_page(processed, page_idx)
        all_tokens.extend(tokens)
        if page_idx == 0:
            dpi_scale = page_dpi_scale

    text_blocks = build_text_blocks(all_tokens)
    table_rows = build_table_rows(text_blocks, dpi_scale=dpi_scale)

    page_section_map = detect_page_sections(table_rows, text_blocks)

    # detect_year_column now returns three values
    year_x_map, full_year_map, col_pitch_map = detect_year_column(table_rows)

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

    validated = validate_fields(repaired)

    return export_fields(validated)


def main():
    parser = argparse.ArgumentParser(description="OCR financial document extractor")
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG), help="Field config YAML")
    parser.add_argument("--output", help="Write JSON output to this file instead of stdout")
    parser.add_argument("--optimize", action="store_true", help="Enable optimization mode")
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
