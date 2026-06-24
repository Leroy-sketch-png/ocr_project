import os
import shutil
from pathlib import Path
from typing import Any, Dict


class RuntimeConfigurationError(RuntimeError):
    """Raised when the local runtime is missing a required dependency."""


def get_tesseract_cmd() -> str:
    """
    Resolve the Tesseract executable path.

    Priority:
    1. TESSERACT_CMD environment variable
    2. tesseract discovered on PATH
    3. Let pytesseract rely on the default executable name
    """
    env_path = os.environ.get("TESSERACT_CMD", "").strip()
    if env_path:
        return env_path

    discovered = shutil.which("tesseract")
    if discovered:
        return discovered

    return "tesseract"


def validate_runtime(engine_name: str = "tesseract") -> None:
    """
    Validate the local OCR runtime before the pipeline starts.

    This keeps failures user-facing and actionable instead of surfacing as a
    traceback deep inside pytesseract or PaddleOCR initialization.
    """
    tesseract_cmd = get_tesseract_cmd()

    if tesseract_cmd == "tesseract" and shutil.which("tesseract") is None:
        raise RuntimeConfigurationError(
            "Tesseract OCR was not found. Install Tesseract or set TESSERACT_CMD "
            "to the full executable path before running the pipeline."
        )

    if tesseract_cmd != "tesseract":
        tesseract_path = Path(tesseract_cmd)
        if not tesseract_path.exists():
            raise RuntimeConfigurationError(
                f"Tesseract OCR executable not found at: {tesseract_cmd}. "
                "Update TESSERACT_CMD to a valid path or install Tesseract on PATH."
            )

    if engine_name.lower() in ("paddle", "neural", "train"):
        try:
            import paddleocr  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise RuntimeConfigurationError(
                "PaddleOCR is not installed. Either install the optional "
                "'paddleocr' dependency or run with --engine tesseract."
            ) from exc


def inspect_runtime(engine_name: str = "tesseract") -> Dict[str, Any]:
    """
    Collect a friendly runtime readiness report for CLI diagnostics.
    """
    report: Dict[str, Any] = {
        "ok": True,
        "engine": engine_name,
        "checks": {},
        "remediation": [],
    }

    tesseract_cmd = get_tesseract_cmd()
    if tesseract_cmd == "tesseract":
        if shutil.which("tesseract") is None:
            report["ok"] = False
            report["checks"]["tesseract"] = {
                "status": "missing",
                "message": "Tesseract was not found on PATH.",
            }
            report["remediation"].append(
                "Install Tesseract OCR or set TESSERACT_CMD to the executable path."
            )
        else:
            report["checks"]["tesseract"] = {
                "status": "ready",
                "message": "Tesseract is available on PATH.",
            }
    else:
        tesseract_path = Path(tesseract_cmd)
        if tesseract_path.exists():
            report["checks"]["tesseract"] = {
                "status": "ready",
                "message": f"Tesseract configured at {tesseract_cmd}.",
            }
        else:
            report["ok"] = False
            report["checks"]["tesseract"] = {
                "status": "missing",
                "message": f"Tesseract executable not found at {tesseract_cmd}.",
            }
            report["remediation"].append(
                "Update TESSERACT_CMD to a valid path or install Tesseract on PATH."
            )

    if engine_name.lower() in ("paddle", "neural", "train"):
        try:
            import paddleocr  # type: ignore  # noqa: F401
        except ImportError:
            report["ok"] = False
            report["checks"]["paddleocr"] = {
                "status": "missing",
                "message": "PaddleOCR is not installed.",
            }
            report["remediation"].append(
                "Install the optional 'paddleocr' dependency or use --engine tesseract."
            )
        else:
            report["checks"]["paddleocr"] = {
                "status": "ready",
                "message": "PaddleOCR is installed.",
            }
    else:
        report["checks"]["paddleocr"] = {
            "status": "not_requested",
            "message": "PaddleOCR is not required for the selected engine.",
        }

    if report["ok"]:
        report["summary"] = (
            "Environment looks ready for the selected OCR engine."
        )
    else:
        report["summary"] = (
            "Environment needs a small setup step before the OCR pipeline can run."
        )

    return report
