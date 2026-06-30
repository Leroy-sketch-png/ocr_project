from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PROJECT_DIR.parent
TESTS_DIR = PROJECT_DIR / "tests"

sys.path.append(str(PROJECT_DIR))

from src.main import process_file  # noqa: E402


SAMPLES = [
    {
        "name": "sample1",
        "pdf": "AA_SAMPLE1.pdf",
        "pdf_path": WORKSPACE_DIR / "AA_SAMPLE1.pdf",
        "gt": TESTS_DIR / "hand_labeled_gt" / "sample1_gt.json",
    },
    {
        "name": "sample2",
        "pdf": "AA_SAMPLE2.pdf",
        "pdf_path": WORKSPACE_DIR / "AA_SAMPLE2.pdf",
        "gt": TESTS_DIR / "hand_labeled_gt" / "sample2_gt.json",
    },
    {
        "name": "sample3",
        "pdf": "AA_SAMPLE3.pdf",
        "pdf_path": WORKSPACE_DIR / "AA_SAMPLE3.pdf",
        "gt": TESTS_DIR / "hand_labeled_gt" / "sample3_gt.json",
    },
]


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_values(extracted: Any, expected: Any) -> bool:
    if expected is None and extracted is None:
        return True
    if expected is None or extracted is None:
        return False
    if isinstance(expected, str) and isinstance(extracted, str):
        return expected.lower() == extracted.lower()
    try:
        return abs(float(extracted) - float(expected)) < 0.01
    except (ValueError, TypeError):
        return str(extracted) == str(expected)


def evaluate_sample(sample: Dict[str, Any]) -> Dict[str, Any]:
    config_path = PROJECT_DIR / "src" / "field_config.yaml"
    result = process_file(str(sample["pdf_path"]), str(config_path), optimization_mode=True)
    gt_data = load_json(sample["gt"])

    fields = {k: v for k, v in result.items() if k != "error"}
    total_expected = 0
    total_extracted = 0
    total_correct = 0
    mismatches: List[Dict[str, Any]] = []

    for field_name, expected_val in gt_data.items():
        extracted_data = fields.get(field_name, {})
        extracted_val = extracted_data.get("value")

        if expected_val is not None:
            total_expected += 1
        if extracted_val is not None:
            total_extracted += 1

        if expected_val is None and extracted_val is None:
            # True Negative, does not affect Precision/Recall totals
            continue
        elif expected_val is None and extracted_val is not None:
            # False Positive
            mismatches.append(
                {
                    "field": field_name,
                    "expected": expected_val,
                    "extracted": extracted_val,
                    "raw_text": extracted_data.get("raw_text"),
                }
            )
        elif expected_val is not None and extracted_val is None:
            # False Negative
            mismatches.append(
                {
                    "field": field_name,
                    "expected": expected_val,
                    "extracted": extracted_val,
                    "raw_text": extracted_data.get("raw_text"),
                }
            )
        else:
            # Both have values, compare them
            if compare_values(extracted_val, expected_val):
                total_correct += 1
            else:
                mismatches.append(
                    {
                        "field": field_name,
                        "expected": expected_val,
                        "extracted": extracted_val,
                        "raw_text": extracted_data.get("raw_text"),
                    }
                )

    precision = (total_correct / total_extracted * 100) if total_extracted else 0.0
    recall = (total_correct / total_expected * 100) if total_expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "sample": sample["name"],
        "pdf": sample["pdf"],
        "total_expected": total_expected,
        "total_extracted": total_extracted,
        "total_correct": total_correct,
        "precision": round(precision, 2),
        "recall": round(recall, 2),
        "f1": round(f1, 2),
        "error": result.get("error"),
        "mismatches": mismatches,
        "output_fields": list(fields.keys()),
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Evaluation Report")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append("")
    lines.append("## Overall")
    lines.append(f"- Expected fields: {report['overall']['total_expected']}")
    lines.append(f"- Extracted fields: {report['overall']['total_extracted']}")
    lines.append(f"- Correct fields: {report['overall']['total_correct']}")
    lines.append(f"- Precision: {report['overall']['precision']:.2f}%")
    lines.append(f"- Recall: {report['overall']['recall']:.2f}%")
    lines.append(f"- F1: {report['overall']['f1']:.2f}%")
    lines.append("")
    for sample in report["samples"]:
        lines.append(f"## {sample['sample']}")
        lines.append(f"- PDF: `{sample['pdf']}`")
        lines.append(f"- Precision: {sample['precision']:.2f}%")
        lines.append(f"- Recall: {sample['recall']:.2f}%")
        lines.append(f"- F1: {sample['f1']:.2f}%")
        lines.append(f"- Expected fields: {sample['total_expected']}")
        lines.append(f"- Extracted fields: {sample['total_extracted']}")
        lines.append(f"- Correct fields: {sample['total_correct']}")
        if sample["error"]:
            lines.append(f"- Error: {sample['error']}")
        if sample["mismatches"]:
            lines.append("- Mismatches:")
            for mismatch in sample["mismatches"]:
                lines.append(
                    f"  - {mismatch['field']}: expected `{mismatch['expected']}` got `{mismatch['extracted']}`"
                )
        lines.append("")
    lines.append("## Notes")
    lines.append("- This report is generated from the bundled ground-truth files.")
    lines.append("- The OCR output artifact is written separately for direct review.")
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    samples = [evaluate_sample(sample) for sample in SAMPLES]
    overall_expected = sum(sample["total_expected"] for sample in samples)
    overall_extracted = sum(sample["total_extracted"] for sample in samples)
    overall_correct = sum(sample["total_correct"] for sample in samples)
    overall_precision = (
        (overall_correct / overall_extracted * 100) if overall_extracted else 0.0
    )
    overall_recall = (
        (overall_correct / overall_expected * 100) if overall_expected else 0.0
    )
    overall_f1 = (
        (2 * overall_precision * overall_recall / (overall_precision + overall_recall))
        if (overall_precision + overall_recall)
        else 0.0
    )
    report = {
        "generated_at": generated_at,
        "overall": {
            "total_expected": overall_expected,
            "total_extracted": overall_extracted,
            "total_correct": overall_correct,
            "precision": round(overall_precision, 2),
            "recall": round(overall_recall, 2),
            "f1": round(overall_f1, 2),
        },
        "samples": samples,
    }

    artifacts_dir = PROJECT_DIR / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    json_path = artifacts_dir / "evaluation_report.json"
    md_path = artifacts_dir / "evaluation_report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
