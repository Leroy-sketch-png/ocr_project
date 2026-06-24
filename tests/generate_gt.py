import json
import os

from paddleocr import PaddleOCR
from pdfplumber import open as pdf_open


def extract_pdf_text_paddle(pdf_path: str):
    ocr = PaddleOCR(use_textline_orientation=True, lang="en")
    doc = pdf_open(pdf_path)

    pages_data = []
    for page_idx in range(len(doc.pages)):
        img = doc.pages[page_idx].to_image(resolution=200).original
        # Save temp image for paddleocr
        temp_img_path = f"temp_page_{page_idx}.png"
        img.save(temp_img_path)

        result = ocr.ocr(temp_img_path)
        if result and result[0]:
            lines = [line[1][0] for line in result[0]]
            pages_data.append({"page": page_idx, "lines": lines})

        os.remove(temp_img_path)

    return pages_data


def main():
    samples = [
        r"c:\Users\c-leroy.phan\Downloads\ai\AA_SAMPLE1.pdf",
        r"c:\Users\c-leroy.phan\Downloads\ai\AA_SAMPLE2.pdf",
        r"c:\Users\c-leroy.phan\Downloads\ai\AA_SAMPLE3.pdf",
    ]

    for sample in samples:
        print(f"Processing {sample}...")
        try:
            data = extract_pdf_text_paddle(sample)
            out_path = sample.replace(".pdf", "_paddle_raw.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"Saved {out_path}")
        except Exception as e:
            print(f"Failed {sample}: {e}")


if __name__ == "__main__":
    main()
