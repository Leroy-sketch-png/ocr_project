import os
import shutil


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
