import argparse
import json
import os
from typing import Any, Dict, List

from .exporter import field_value_to_dict
from .field_extractor import extract_fields, load_field_config
from .io_loader import load_document
from .ocr_engine import get_ocr_engine
from .table_builder import build_table_rows, build_text_blocks
from .validator import is_ocr_failure, validate_fields
from .value_parser import parse_numeric_fields


def process_file(
    path: str, config_path: str, engine_name: str = "tesseract"
) -> Dict[str, Any]:
    try:
        doc = load_document(path)
    except Exception as e:
        return {"error": str(e)}

    ocr_engine = get_ocr_engine(engine_name)

    all_tokens = []
    for page_idx, pil_image in doc.pages:
        page_tokens = ocr_engine.recognize_page(pil_image, page_idx)
        all_tokens.extend(page_tokens)

    if is_ocr_failure(all_tokens):
        return {"error": "OCR extraction failed"}

    blocks = build_text_blocks(all_tokens)
    table_rows = build_table_rows(blocks)

    field_defs = load_field_config(config_path)

    # Required fields derived from config
    required_fields: List[str] = []
    for sec, fields in field_defs.items():
        if isinstance(fields, dict):
            required_fields.extend(fields.keys())

    field_values = extract_fields(table_rows, blocks, field_defs)
    parse_numeric_fields(field_values)

    validation_result = validate_fields(field_values, required_fields)

    output: Dict[str, Any] = {
        name: field_value_to_dict(fv) for name, fv in field_values.items()
    }
    if "error" in validation_result:
        output["error"] = validation_result["error"]
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline OCR Pipeline")
    parser.add_argument("file_path", help="Path to PDF or Image file")
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(__file__), "field_config.yaml"),
    )
    parser.add_argument("--engine", default="tesseract")

    args = parser.parse_args()

    result = process_file(args.file_path, args.config, args.engine)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
