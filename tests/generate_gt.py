import json
import os

from pdfplumber import open as pdf_open

try:
    from paddleocr import PaddleOCR
except ImportError:  # pragma: no cover - optional helper dependency
    PaddleOCR = None


def extract_pdf_text_paddle(pdf_path: str):
    if PaddleOCR is None:
        raise RuntimeError(
            "PaddleOCR is required to generate ground truth with this helper."
        )

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
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workspace_dir = os.path.dirname(project_dir)
    samples = [
        os.path.join(workspace_dir, "AA_SAMPLE1.pdf"),
        os.path.join(workspace_dir, "AA_SAMPLE2.pdf"),
        os.path.join(workspace_dir, "AA_SAMPLE3.pdf"),
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
