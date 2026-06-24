import pytesseract
from pytesseract import Output
from PIL import Image

pytesseract.pytesseract.tesseract_cmd = r'C:\Users\c-leroy.phan\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'

def targeted_ocr(pil_image: Image.Image, bbox: tuple) -> str:
    """
    Crops a specific bounding box from the image and runs highly constrained Tesseract on it.
    bbox is (x1, y1, x2, y2).
    """
    x1, y1, x2, y2 = bbox
    
    # Add a slight padding to the bbox
    padding = 4
    width, height = pil_image.size
    
    crop_x1 = max(0, x1 - padding)
    crop_y1 = max(0, y1 - padding)
    crop_x2 = min(width, x2 + padding)
    crop_y2 = min(height, y2 + padding)
    
    cropped_img = pil_image.crop((crop_x1, crop_y1, crop_x2, crop_y2))
    
    # Tesseract configuration:
    # --psm 7: Treat the image as a single text line.
    # tessedit_char_whitelist: Limit to numbers and common punctuation.
    # load_system_dawg=F, load_freq_dawg=F: Disable dictionaries.
    config = (
        "--psm 7 "
        "-c tessedit_char_whitelist=0123456789(),.- "
        "-c load_system_dawg=F "
        "-c load_freq_dawg=F"
    )
    
    text = pytesseract.image_to_string(cropped_img, config=config).strip()
    return text
