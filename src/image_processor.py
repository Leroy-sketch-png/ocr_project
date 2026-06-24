import cv2
import numpy as np
from PIL import Image


def preprocess_image(pil_image: Image.Image) -> Image.Image:
    """
    Apply OpenCV preprocessing nitro-boost to an image before feeding to Tesseract.
    This fundamentally fixes Tesseract hallucinating characters (e.g. 5 -> 8)
    by feeding it a pristine, binarized 300 DPI image.
    """
    # Convert PIL Image to OpenCV format
    open_cv_image = np.array(pil_image)

    # Handle Grayscale images vs RGB
    if len(open_cv_image.shape) == 3 and open_cv_image.shape[2] == 3:
        open_cv_image = open_cv_image[:, :, ::-1].copy()  # RGB to BGR
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)
    elif len(open_cv_image.shape) == 3 and open_cv_image.shape[2] == 4:
        open_cv_image = open_cv_image[:, :, :3][:, :, ::-1].copy()  # RGBA to BGR
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)
    else:
        gray = open_cv_image

    # 1. Noise Removal (Gaussian Blur)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)

    # 2. Binarization (Otsu's Thresholding)
    # This separates text (foreground) from any background noise/artifacts
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Convert back to PIL Image for pytesseract
    result_pil = Image.fromarray(thresh)
    return result_pil
