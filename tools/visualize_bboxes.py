"""
Overlay extraction bounding boxes onto PDF pages for visual verification.

Usage:
  python tools/visualize_bboxes.py <pdf> <json> [--output-dir out/]

Outputs one PNG per page with labelled rectangles around extracted values.
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.io_loader import load_document
from PIL import Image, ImageDraw, ImageFont

_COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
    "#469990", "#dcbeff", "#9a6324", "#fffac8", "#800000",
]


def _flatten_fields(data: dict) -> list[dict]:
    rows = []
    for fname, fv in data.items():
        if fv is None:
            continue
        years = fv.get("years") or {}
        if years:
            for yk, yv in years.items():
                rows.append({**yv, "field": fname, "year": yk, "_primary_bbox": fv.get("bbox")})
        else:
            rows.append({**fv, "field": fname, "year": None})
    return rows


def visualize(pdf_path: str, json_path: str, output_dir: str = "bbox_viz"):
    doc = load_document(pdf_path)

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    entries = _flatten_fields(data)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    page_groups: dict[int, list[dict]] = {}
    for e in entries:
        pg = e.get("page")
        if pg is None:
            continue
        page_groups.setdefault(int(pg), []).append(e)

    for page_num in sorted(page_groups):
        pil_img = None
        for pnum, img, _ in doc.pages:
            if pnum == page_num:
                pil_img = img.convert("RGB")
                break
        if pil_img is None:
            print(f"  [SKIP] Page {page_num}")
            continue

        img = pil_img.copy()
        draw = ImageDraw.Draw(img)

        for i, entry in enumerate(page_groups[page_num]):
            bbox = entry.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = bbox
            color = _COLORS[i % len(_COLORS)]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=3)

            label = entry.get("field", "")
            val = entry.get("value", "")
            if val is not None:
                label += f"={val}"
            draw.text((x1, y1 - 16), label, fill=color, font=font)

        path = out / f"page_{page_num}.png"
        img.save(path)
        print(f"  [OK]  Page {page_num} -> {path}")

    print(f"\nDone — {len(page_groups)} pages -> {out.resolve()}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("pdf")
    p.add_argument("json")
    p.add_argument("--output-dir", default="bbox_viz")
    args = p.parse_args()
    visualize(args.pdf, args.json, args.output_dir)
