# Agent Implementation Plan — OCR Project Trainer Feedback (Round 1)

> **Branch to work on:** `fix/trainer-feedback-round1`
> **Base branch:** `V3`
> **PR:** https://github.com/Leroy-sketch-png/ocr_project/pull/1
>
> This document is the single source of truth for the coding agent. Follow every task in order. Do not skip tasks. After completing all tasks, verify the checklist at the bottom before committing.

---

## Context

The trainer reviewed the current output and raised 8 issues — 3 about incorrect/missing extracted values and 5 about code quality. All 8 must be resolved in this branch. The goal is to produce a clean commit that the trainer can re-test against `AA_SAMPLE3.pdf`.

### Repo Structure (relevant files only)

```
src/
  field_config.yaml      ← keyword lists for each field
  field_extractor.py     ← core extraction logic
  repair_engine.py       ← residual/balance repair logic
  ocr_engine.py          ← Tesseract wrapper
  image_processor.py     ← preprocessing pipeline
  main.py                ← pipeline orchestrator
  models.py              ← FieldValue dataclass
  exporter.py            ← JSON serializer
  value_parser.py        ← numeric/date parsing helpers
```

---

## Task List

### TASK 1 — `models.py`: Add `field_label` field to `FieldValue`

**File:** `src/models.py`

Add an optional `field_label` attribute to the `FieldValue` dataclass. It must have a default of `None` so existing call sites that do not set it continue to work without changes.

```python
# Before (approximate existing shape):
@dataclass
class FieldValue:
    value: Any
    page: Optional[int]
    bbox: Optional[list]
    confidence: Optional[float]
    valid: bool
    reason: Optional[str]
    evidence_text: Optional[str]

# After:
@dataclass
class FieldValue:
    value: Any
    page: Optional[int]
    bbox: Optional[list]
    confidence: Optional[float]
    valid: bool
    reason: Optional[str]
    evidence_text: Optional[str]
    field_label: Optional[str] = None   # ← ADD THIS
```

**Acceptance:** `FieldValue(value=1, page=1, bbox=None, confidence=None, valid=True, reason=None, evidence_text=None)` instantiates without error (backwards compatible).

---

### TASK 2 — `exporter.py`: Include `field_label` as first key in output dict

**File:** `src/exporter.py`

In the function that serializes a `FieldValue` to a dict (likely named `to_dict`, `serialize`, or inside a list comprehension), add `"field_label": f.field_label` as the **first key** so the exact document label is the first thing reviewers see in the JSON.

```python
# Example — adapt to whatever the actual function looks like:
def export_field(f: FieldValue) -> dict:
    return {
        "field_label": f.field_label,   # ← MOVE/ADD to first position
        "value": f.value,
        "page": f.page,
        "bbox": f.bbox,
        "confidence": f.confidence,
        "valid": f.valid,
        "reason": f.reason,
        "evidence_text": f.evidence_text,
    }
```

**Acceptance:** Running the pipeline on any sample PDF produces JSON where `field_label` is the first key in every field object.

---

### TASK 3 — `field_extractor.py`: Store `field_label` on every extracted `FieldValue`

**File:** `src/field_extractor.py`

Wherever a `FieldValue` is constructed after a successful label match, set `field_label` to the exact text from the document row/label that triggered the match.

- For standard numeric fields: `field_label = row.description` (or whatever attribute holds the row's text label).
- For **Auditor's Opinion** specifically: `field_label = "Auditor's Opinion"` (hard-coded literal, since the opinion type is inferred, not directly read from a single cell).

---

### TASK 4 — `field_extractor.py`: Fix Auditor's Opinion overwrite bug

**File:** `src/field_extractor.py`

**Problem:** The outer `for block in text_blocks` loop (or equivalent) does not `break` after a valid opinion match. A later block that contains a keyword but produces a weaker/wrong match silently overwrites the correct result.

**Fix:** Introduce a `found_opinion` boolean flag before the outer loop. Set it to `True` and `break` the outer loop immediately after storing a valid opinion result.

```python
# Pseudocode — adapt to the actual loop structure:
found_opinion = False
for block in text_blocks:
    for keyword in OPINION_KEYWORDS:
        if keyword in block.text:
            opinion_value = _parse_opinion_type(block.text)
            if opinion_value:
                result["auditors_opinion"] = FieldValue(
                    value=opinion_value,
                    field_label="Auditor's Opinion",
                    # ... other fields ...
                )
                found_opinion = True
                break   # ← break inner loop
    if found_opinion:
        break           # ← break outer loop
```

**Acceptance:** Running on a document where "Opinion" appears in multiple blocks always returns the value from the **first** valid match.

---

### TASK 5 — `field_extractor.py`: Move inline import to top of file

**File:** `src/field_extractor.py`

Find the `from .value_parser import parse_numeric` (or equivalent) import that is currently inside a nested loop or inner function body. Cut it and paste it at the top of the file with the other imports.

```python
# Top of field_extractor.py — add alongside existing imports:
from .value_parser import parse_numeric
```

Delete the original inline import.

**Acceptance:** No `import` statements appear inside any function body or loop in `field_extractor.py`.

---

### TASK 6 — `repair_engine.py`: Replace `residual == 0.0` with tolerance check

**File:** `src/repair_engine.py`

**Problem:** Floating-point arithmetic means `residual` is almost never exactly `0.0`. The equality check causes the repair loop to continue unnecessarily.

**Fix:** Define a module-level tolerance constant and a small helper, then replace every `residual == 0.0` (and `residual == 0`, `residual != 0.0`, etc.) with calls to that helper.

```python
# Add near the top of repair_engine.py, after imports:
_RESIDUAL_TOLERANCE = 1e-2   # tweak if needed

def _residual_ok(residual: float) -> bool:
    """Return True when residual is close enough to zero."""
    return abs(residual) < _RESIDUAL_TOLERANCE
```

Then replace all occurrences:

| Old expression | New expression |
|---|---|
| `residual == 0.0` | `_residual_ok(residual)` |
| `residual == 0` | `_residual_ok(residual)` |
| `residual != 0.0` | `not _residual_ok(residual)` |
| `if residual:` (where residual is the float) | `if not _residual_ok(residual):` |

Search the entire file — these checks likely appear in Phase 1, Phase 1.5, and Phase 2 blocks.

**Acceptance:** No bare `== 0.0`, `== 0`, or `!= 0.0` comparisons remain on `residual` anywhere in `repair_engine.py`.

---

### TASK 7 — `repair_engine.py`: Replace all `print()` with `logging.debug()`

**File:** `src/repair_engine.py`

**Problem:** Debug `print()` statements pollute stdout during normal runs.

**Fix:**

1. Add at the top of the file (after existing imports):
   ```python
   import logging
   logger = logging.getLogger(__name__)
   ```

2. Replace every `print(...)` in the file with `logger.debug(...)`. Keep the message text — just change the call.
   ```python
   # Before:
   print(f"[repair] residual after phase 1: {residual}")
   # After:
   logger.debug("residual after phase 1: %s", residual)
   ```
   Use `%s`-style formatting in `logger.debug()` (lazy evaluation — more efficient than f-strings in logging calls).

**Acceptance:** Zero `print(` calls remain in `repair_engine.py`. Debug output is silent when the pipeline runs normally.

---

### TASK 8 — `ocr_engine.py` + `main.py`: Eliminate double preprocessing

**Files:** `src/ocr_engine.py`, `src/main.py`

**Problem:** `TesseractEngine.recognize_page` internally calls `preprocess_image()`. `main.py` also calls `preprocess_image()` to build the `processed_images` cache. Every page is therefore preprocessed twice.

**Fix — two-part:**

**Part A — `ocr_engine.py`:**

Remove (or guard with a parameter) the internal `preprocess_image()` call inside `recognize_page`. The method should now expect a pre-processed image and document this in its docstring.

```python
def recognize_page(self, preprocessed_image, page_number: int) -> list[WordToken]:
    """
    Run Tesseract OCR on a pre-processed image.

    Args:
        preprocessed_image: A preprocessed image (numpy array or PIL Image).
                            Caller is responsible for preprocessing ONCE before
                            passing here. Do NOT pass raw page images.
        page_number: 1-based page index, stored on every returned WordToken.
    """
    # preprocessing done once in main.py
    data = pytesseract.image_to_data(preprocessed_image, output_type=Output.DICT)
    # ... rest of method unchanged ...
```

**Part B — `main.py`:**

Ensure `process_file` (or the equivalent orchestration function) builds `processed_images` once and passes those cached images to **both** the OCR engine and the repair engine.

```python
def process_file(pdf_path, field_configs):
    # Step 1: load raw pages
    raw_pages = load_pages(pdf_path)          # returns list of PIL images

    # Step 2: preprocess ONCE — build a cache
    processed_images = {
        page_num: preprocess_image(raw_page)
        for page_num, raw_page in enumerate(raw_pages, start=1)
    }

    # Step 3: OCR using cached preprocessed images
    all_words = []
    for page_num, proc_img in processed_images.items():
        words = ocr_engine.recognize_page(proc_img, page_num)  # no re-preprocessing
        all_words.extend(words)

    # Step 4: field extraction + repair also use the same cached images
    # (pass processed_images to repair_engine if it needs page images)
    ...
```

**Acceptance:** `preprocess_image` is called exactly **once per page** per document. Add a comment `# preprocessing done once in main.py` near where it was removed in `ocr_engine.py`.

---

### TASK 9 — `field_config.yaml`: Expand keywords for three weak fields

**File:** `src/field_config.yaml`

Add the following label variants to improve extraction coverage. Append to the existing `label_keywords` list for each field — do not replace existing keywords.

#### `plant_and_equipment`

```yaml
- "Property, plant and equipment"
- "PP&E"
- "Plant, property and equipment"
- "Plant, machinery and equipment"
- "Fixtures and fittings"
- "Machinery and equipment"
- "Right-of-use assets"
```

#### `trade_receivables`

```yaml
- "Trade receivables"
- "Trade and other receivables"
- "Trade and other current receivables"
- "Receivables from customers"
- "Net trade receivables"
- "Accounts receivable"
```

#### `auditors_opinion`

```yaml
- "Independent Auditor's Report"
- "Independent auditor"
- "Unmodified opinion"
- "Unqualified opinion"
- "Qualified opinion"
- "Basis for Opinion"
- "Key Audit Matters"
- "Emphasis of Matter"
```

---

## Verification Checklist

Before committing, confirm all of the following:

- [ ] `models.py` — `FieldValue` has `field_label: Optional[str] = None`
- [ ] `exporter.py` — `field_label` is the **first** key in every serialized field dict
- [ ] `field_extractor.py` — `field_label` is set for every constructed `FieldValue`
- [ ] `field_extractor.py` — Auditor's Opinion outer loop breaks on first valid match
- [ ] `field_extractor.py` — no `import` inside any function body or loop
- [ ] `repair_engine.py` — `_RESIDUAL_TOLERANCE` and `_residual_ok()` defined at module level
- [ ] `repair_engine.py` — zero bare `residual == 0.0` / `residual == 0` comparisons remain
- [ ] `repair_engine.py` — zero `print(` calls remain; all replaced with `logger.debug(...)`
- [ ] `ocr_engine.py` — `recognize_page` does NOT call `preprocess_image()` internally
- [ ] `main.py` — `processed_images` dict is built once; passed to both OCR and repair
- [ ] `field_config.yaml` — new keywords added for Plant and Equipment, Trade Receivables, Auditor's Opinion
- [ ] Run pipeline on `AA_SAMPLE3.pdf` — output JSON shows `field_label` on every field
- [ ] No `print` output in terminal during a normal run (only appears with `--log-level DEBUG`)

---

## Commit Message

```
fix: trainer feedback round 1 — label output, repair precision, extractor bugs, preprocessing cache
```

Push to `fix/trainer-feedback-round1`. The PR is already open at https://github.com/Leroy-sketch-png/ocr_project/pull/1.
