"""Evaluation script for the OCR extraction pipeline.

Scoring rules:
- expected=X,    extracted=X    -> correct        (score 100)
- expected=X,    extracted=Y    -> wrong           (score 0)
- expected=None, extracted=None -> correct         (score 100)
- expected=None, extracted=X   -> false positive   (score 0)
- expected=X,    extracted=None -> missing         (score 0)

Numeric tolerance:
  abs(diff) <= 1.0  OR  rel(diff) <= 0.01%
  This keeps the scorer consistent across documents denominated in
  thousands vs tens of millions (avoids the old flat ±1.0 being
  trivially loose on large-scale documents).
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

GROUND_TRUTH_PATH = Path("tests/ground_truth.json")
SAMPLES = {
    "SAMPLE1": Path("../AA_SAMPLE1.pdf"),
    "SAMPLE2": Path("../AA_SAMPLE2.pdf"),
    "SAMPLE3": Path("../AA_SAMPLE3.pdf"),
}
_ABS_TOLERANCE = 1.0      # always accept diff within 1 unit
_REL_TOLERANCE = 1e-4     # accept diff within 0.01% of expected magnitude


def load_ground_truth() -> Dict[str, Any]:
    with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_pipeline(pdf_path: Path) -> Dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "src.main", str(pdf_path), "--optimize"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Pipeline failed: {result.stderr}")
    return json.loads(result.stdout)


def score_field(expected: Any, extracted: Any) -> int:
    """Return 100 (correct) or 0 (wrong / missing / false-positive)."""
    if expected is None and extracted is None:
        return 100
    if expected is None and extracted is not None:
        return 0   # false positive
    if expected is not None and extracted is None:
        return 0   # missing
    # String fields (e.g. Auditor's Opinion)
    if isinstance(expected, str) or isinstance(extracted, str):
        return 100 if str(expected) == str(extracted) else 0
    # Numeric: accept if within absolute OR relative tolerance
    diff = abs(float(extracted) - float(expected))
    rel  = diff / max(abs(float(expected)), 1.0)
    return 100 if (diff <= _ABS_TOLERANCE or rel <= _REL_TOLERANCE) else 0


def evaluate() -> None:
    gt = load_ground_truth()
    total_correct = 0
    total_fields = 0

    for sample_name, pdf_path in SAMPLES.items():
        if not pdf_path.exists():
            print(f"[SKIP] {sample_name}: file not found at {pdf_path}")
            continue

        print(f"\n=== {sample_name} ===")
        extracted = run_pipeline(pdf_path)
        sample_gt = gt.get(sample_name, {})

        for field_name, expected_val in sample_gt.items():
            raw_extracted = extracted.get(field_name)
            if isinstance(raw_extracted, dict):
                extracted_val = raw_extracted.get("value")
            else:
                extracted_val = raw_extracted

            score = score_field(expected_val, extracted_val)
            total_correct += score // 100
            total_fields += 1

            status = "✅" if score == 100 else "❌"
            tag = ""
            if score == 0:
                if expected_val is None and extracted_val is not None:
                    tag = " [FALSE POSITIVE]"
                elif expected_val is not None and extracted_val is None:
                    tag = " [MISSING]"
                else:
                    tag = " [WRONG VALUE]"
            print(
                f"  {status} {field_name}: expected={expected_val} "
                f"extracted={extracted_val}{tag}"
            )

    print(f"\n--- TOTAL: {total_correct}/{total_fields} correct ---")
    if total_fields:
        print(f"--- SCORE: {100 * total_correct / total_fields:.1f}% ---")


if __name__ == "__main__":
    evaluate()
