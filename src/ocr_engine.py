from abc import ABC, abstractmethod
from typing import Any, List

import pytesseract  # type: ignore
from pytesseract import Output  # type: ignore

from .image_processor import preprocess_image
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
        processed_image = preprocess_image(pil_image)
        data = pytesseract.image_to_data(processed_image, output_type=Output.DICT)
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

        from paddleocr import PaddleOCR  # type: ignore

        # Suppress verbose paddleocr logging
        logging.getLogger("ppocr").setLevel(logging.ERROR)

        # Initialize PaddleOCR engine
        self.ocr = PaddleOCR(use_textline_orientation=True, lang="en")

    def recognize_page(self, pil_image: Any, page: int) -> List[Token]:
        import re

        import numpy as np

        # Convert PIL image to RGB numpy array
        img_np = np.array(pil_image.convert("RGB"))

        # Run PaddleOCR
        result = self.ocr.ocr(img_np, cls=True)
        if not result or result[0] is None:
            return []

        tokens = []
        # Since we passed a single image, the results for that image are in result[0]
        for line in result[0]:
            if not line or len(line) < 2:
                continue

            box, (text, conf) = line
            if not text:
                continue

            # Map coordinates: box is a list of 4 points: [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
            xs = [pt[0] for pt in box]
            ys = [pt[1] for pt in box]
            x1 = int(min(xs))
            y1 = int(min(ys))
            x2 = int(max(xs))
            y2 = int(max(ys))

            # Scale confidence to 0-100 like Tesseract
            scaled_conf = float(conf) * 100.0

            # Split line-level token into word-level tokens
            words = []
            for match in re.finditer(r"\S+", text):
                words.append((match.group(), match.start(), match.end()))

            if not words:
                continue

            W = x2 - x1
            L = len(text)

            for word_text, start_idx, end_idx in words:
                # Interpolate x coordinates
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
    elif name in ("paddle", "train"):
        return PaddleEngine()
    raise ValueError(f"Unknown OCR engine: {engine_name}")
