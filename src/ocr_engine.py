from abc import ABC, abstractmethod
from typing import Any, List
import pytesseract  # type: ignore
from pytesseract import Output  # type: ignore

from .models import Token

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Users\c-leroy.phan\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
)


class OCREngine(ABC):
    """Abstract base class for OCR engines."""

    @abstractmethod
    def recognize_page(self, pil_image: Any, page: int) -> List[Token]:
        """Recognize text on a given page."""
        pass


class TesseractEngine(OCREngine):
    """Tesseract implementation of OCREngine."""

    def recognize_page(self, pil_image: Any, page: int) -> List[Token]:
        data = pytesseract.image_to_data(pil_image, output_type=Output.DICT)
        tokens = []
        n = len(data["text"])
        for i in range(n):
            txt = data["text"][i].strip()
            if not txt:
                continue
            conf = float(data["conf"][i])
            x = data["left"][i]
            y = data["top"][i]
            w = data["width"][i]
            h = data["height"][i]
            tokens.append(
                Token(
                    text=txt,
                    page=page,
                    bbox=(x, y, x + w, y + h),
                    confidence=conf,
                )
            )
        return tokens


def get_ocr_engine(engine_name: str = "tesseract") -> OCREngine:
    if engine_name.lower() == "tesseract":
        return TesseractEngine()
    raise ValueError(f"Unknown OCR engine: {engine_name}")
