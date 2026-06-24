import json
import os
import sys
from typing import Any, Dict

# Add parent directory to path so we can import src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.main import process_file


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compare_values(extracted, expected) -> bool:
    if expected is None and extracted is None:
        return True
    if expected is None or extracted is None:
        return False
    if isinstance(expected, str) and isinstance(extracted, str):
        return (
            expected.lower() in extracted.lower()
            or extracted.lower() in expected.lower()
        )

    try:
        # allow small floating point differences
        return abs(float(extracted) - float(expected)) < 0.01
    except (ValueError, TypeError):
        return str(extracted) == str(expected)


def evaluate(extreme_mode=False):
    samples = [
        {
            "pdf": r"c:\Users\c-leroy.phan\Downloads\ai\AA_SAMPLE1.pdf",
            "gt": r"c:\Users\c-leroy.phan\Downloads\ai\ocr_project\tests\hand_labeled_gt\sample1_gt.json",
        },
        {
            "pdf": r"c:\Users\c-leroy.phan\Downloads\ai\AA_SAMPLE2.pdf",
            "gt": r"c:\Users\c-leroy.phan\Downloads\ai\ocr_project\tests\hand_labeled_gt\sample2_gt.json",
        },
        {
            "pdf": r"c:\Users\c-leroy.phan\Downloads\ai\AA_SAMPLE3.pdf",
            "gt": r"c:\Users\c-leroy.phan\Downloads\ai\ocr_project\tests\hand_labeled_gt\sample3_gt.json",
        },
    ]

    total_expected = 0
    total_extracted = 0
    total_correct = 0

    mode_name = "EXTREME" if extreme_mode else "CLEAN"
    print(f"\n[{mode_name} MODE]")

    for sample in samples:
        pdf_path = sample["pdf"]
        gt_path = sample["gt"]

        if not os.path.exists(gt_path):
            print(f"Skipping {pdf_path}, no ground truth found at {gt_path}")
            continue

        print(f"Evaluating {os.path.basename(pdf_path)}...")
        gt_data = load_json(gt_path)

        try:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "src",
                "field_config.yaml",
            )
            result = process_file(pdf_path, config_path, extreme_mode=extreme_mode)
        except Exception as e:
            print(f"Pipeline crashed on {pdf_path}: {e}")
            continue

        fields = {k: v for k, v in result.items() if k != "error"}

        for field_name, expected_val in gt_data.items():
            if expected_val is not None:
                total_expected += 1

                extracted_data = fields.get(field_name, {})
                extracted_val = extracted_data.get("value")

                if extracted_val is not None:
                    total_extracted += 1

                if compare_values(extracted_val, expected_val):
                    total_correct += 1
                else:
                    print(
                        f"  [MISMATCH] {field_name}: Expected {expected_val}, Got {extracted_val} (Raw: {extracted_data.get('raw_text')})"
                    )

    precision = (total_correct / total_extracted * 100) if total_extracted > 0 else 0
    recall = (total_correct / total_expected * 100) if total_expected > 0 else 0
    f1 = (
        (2 * precision * recall) / (precision + recall)
        if (precision + recall) > 0
        else 0
    )

    print("\n" + "=" * 40)
    print(f"[ {mode_name} BENCHMARK RESULTS ]")
    print("=" * 40)
    print(f"Total Fields Expected: {total_expected}")
    print(f"Total Fields Extracted: {total_extracted}")
    print(f"Total Fields Correct:   {total_correct}")
    print(f"Precision: {precision:.2f}%")
    print(f"Recall:    {recall:.2f}%")
    print(f"F1-Score:  {f1:.2f}%")
    print("=" * 40)


if __name__ == "__main__":
    evaluate(extreme_mode=False)
    evaluate(extreme_mode=True)
