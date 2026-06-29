from abc import ABC, abstractmethod
from typing import Any, Dict, List

import pytesseract  # type: ignore
from pytesseract import Output  # type: ignore

from .image_processor import preprocess_image
from .models import Token
from .runtime_config import get_tesseract_cmd

pytesseract.pytesseract.tesseract_cmd = get_tesseract_cmd()

try:
    from paddleocr import PaddleOCR  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    PaddleOCR = None


class OCREngine(ABC):
    """Abstract base class for OCR engines."""

    @abstractmethod
    def recognize_page(self, pil_image: Any, page: int) -> List[Token]:
        """Recognize text on a given page image (already preprocessed)."""
        pass


class TesseractEngine(OCREngine):
    """Tesseract implementation of OCREngine.

    NOTE: ``recognize_page`` expects a *preprocessed* image (numpy array or PIL
    image) produced by ``image_processor.preprocess_image``.  The caller in
    ``main.py`` is responsible for preprocessing and caching the image once per
    page so that this method never repeats the preprocessing pipeline.
    """

    def recognize_page(self, pil_image: Any, page: int) -> List[Token]:
        # pil_image is already preprocessed by the caller — do NOT call
        # preprocess_image() here again.
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


class PaddleEngine(OCREngine):
    """PaddleOCR implementation of OCREngine."""

    def __init__(self) -> None:
        import logging

        if PaddleOCR is None:
            raise RuntimeError(
                "PaddleOCR is not installed. Install the optional paddleocr "
                "dependency or use --engine tesseract."
            )

        # Suppress verbose paddleocr logging
        logging.getLogger("ppocr").setLevel(logging.ERROR)

        self.ocr = PaddleOCR(use_textline_orientation=True, lang="en")

    def recognize_page(self, pil_image: Any, page: int) -> List[Token]:
        import re

        import numpy as np

        img_np = np.array(pil_image.convert("RGB"))

        result = self.ocr.ocr(img_np, cls=True)
        if not result or result[0] is None:
            return []

        tokens = []
        for line in result[0]:
            if not line or len(line) < 2:
                continue

            box, (text, conf) = line
            if not text:
                continue

            xs = [pt[0] for pt in box]
            ys = [pt[1] for pt in box]
            x1 = int(min(xs))
            y1 = int(min(ys))
            x2 = int(max(xs))
            y2 = int(max(ys))

            scaled_conf = float(conf) * 100.0

            words = []
            for match in re.finditer(r"\S+", text):
                words.append((match.group(), match.start(), match.end()))

            if not words:
                continue

            W = x2 - x1
            L = len(text)

            for word_text, start_idx, end_idx in words:
                word_x1 = x1 + int((start_idx / L) * W) if L > 0 else x1
                word_x2 = x1 + int((end_idx / L) * W) if L > 0 else x2

                tokens.append(
                    Token(
                        text=word_text,
                        page=page,
                        bbox=(word_x1, y1, word_x2, y2),
                        confidence=scaled_conf,
                    )
                )
        return tokens


def get_ocr_engine(engine_name: str = "tesseract") -> OCREngine:
    name = engine_name.lower()
    if name == "tesseract":
        return TesseractEngine()
    elif name in ("paddle", "neural", "train"):
        return PaddleEngine()
    raise ValueError(f"Unknown OCR engine: {engine_name}")
