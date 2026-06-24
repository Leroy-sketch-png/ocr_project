import os
import shutil
from pathlib import Path


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
