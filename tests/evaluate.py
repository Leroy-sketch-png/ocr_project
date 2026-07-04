"""Evaluation script for the OCR extraction pipeline.

Scoring rules:
- Single-value GT (scalar): same as before, compare vs extraction primary value
- Year-keyed GT (dict): compare each year's value vs extraction years dict
- Null GT: field should be absent or primary value None

Numeric tolerance:
  abs(diff) <= 1.0  OR  rel(diff) <= 0.01%
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
    "KO": Path("../real_10ks/ko_10k.pdf"),
}
_ABS_TOLERANCE = 1.0
_REL_TOLERANCE = 1e-4


def load_ground_truth() -> Dict[str, Any]:
    with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_pipeline(pdf_path: Path) -> Dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "src.main", str(pdf_path), "--optimize"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"Pipeline failed: {result.stderr}")
    if not result.stdout:
        raise RuntimeError(f"Pipeline produced no output: {result.stderr[:500]}")
    return json.loads(result.stdout)


def score_field(expected: Any, extracted: Any) -> int:
    if expected is None and extracted is None:
        return 100
    if expected is None and extracted is not None:
        return 0
    if expected is not None and extracted is None:
        return 0
    if isinstance(expected, str) or isinstance(extracted, str):
        return 100 if str(expected) == str(extracted) else 0
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
            ext_years = (raw_extracted or {}).get("years", {})

            if isinstance(expected_val, dict):
                # Year-keyed GT
                for yr_str, year_expected in expected_val.items():
                    yr_key = str(yr_str)
                    year_extracted = None
                    if yr_key in ext_years:
                        year_extracted = ext_years[yr_key].get("value")

                    score = score_field(year_expected, year_extracted)
                    total_correct += score // 100
                    total_fields += 1

                    status = "OK" if score == 100 else "FAIL"
                    tag = ""
                    if score == 0:
                        if year_expected is None and year_extracted is not None:
                            tag = " [FALSE POSITIVE]"
                        elif year_expected is not None and year_extracted is None:
                            tag = " [MISSING]"
                        elif year_expected is None and year_extracted is None:
                            tag = ""  # both None = correct, already scored 100 above
                        else:
                            tag = " [WRONG VALUE]"
                    print(
                        f"  {status} {field_name} [{yr_str}]: expected={year_expected} "
                        f"extracted={year_extracted}{tag}"
                    )
            else:
                # Scalar GT (null, string, number) — check primary value
                if isinstance(raw_extracted, dict):
                    extracted_val = raw_extracted.get("value")
                else:
                    extracted_val = raw_extracted

                score = score_field(expected_val, extracted_val)
                total_correct += score // 100
                total_fields += 1

                status = "OK" if score == 100 else "FAIL"
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
