import os
from typing import List, Tuple
from PIL import Image
import pdfplumber

from .models import Document


class InvalidInputError(Exception):
    """Exception raised for unsupported or missing files."""


def load_document(file_path: str) -> Document:
    """
    Load a document from a file path.

    Args:
        file_path (str): The path to the file.

    Returns:
        Document: An object containing a list of (page_index, Image.Image) tuples.

    Raises:
        InvalidInputError: If the file does not exist or has an unsupported extension.
    """
    if not os.path.exists(file_path):
        raise InvalidInputError(f"File not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    pages: List[Tuple[int, Image.Image]] = []

    if ext == ".pdf":
        try:
            with pdfplumber.open(file_path) as pdf:
                for idx, page in enumerate(pdf.pages, start=1):
                    # resolution=100 to avoid memory exhaustion and hanging in tesseract
                    img = page.to_image(resolution=100).original
                    pages.append((idx, img))
        except Exception as e:
            raise InvalidInputError(f"Failed to read PDF: {e}")
    elif ext in (".jpg", ".jpeg", ".png"):
        try:
            img = Image.open(file_path)
            img.load()  # ensure image is loaded
            pages.append((1, img))
        except Exception as e:
            raise InvalidInputError(f"Failed to read image: {e}")
    else:
        raise InvalidInputError(f"Unsupported file type: {ext}")

    if not pages:
        raise InvalidInputError(f"No pages found in {file_path}")

    return Document(pages=pages)
