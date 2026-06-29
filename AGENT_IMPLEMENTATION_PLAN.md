# AGENT_IMPLEMENTATION_PLAN.md — Round 1 Submission Verification & Re-Run

## Context

**Branch:** `fix/trainer-feedback-round1`
**PR:** https://github.com/Leroy-sketch-png/ocr_project/pull/1

All trainer Round 1 code fixes are already implemented on this branch.
This plan is for the agent to **verify** each fix is correctly in place,
then **re-run** the pipeline on AA_SAMPLE3.pdf and produce updated output.

> ⚠️ Do NOT re-implement anything. Read each file first. Only act if verification fails.

---

## Part 1 — Verify All 9 Fixes Are Present

### FIX-1 · `src/repair_engine.py` — Float tolerance on residual

**Read the file. Confirm:**
- `_RESIDUAL_TOLERANCE = 1e-2` exists at module level
- `_residual_ok(residual)` helper exists and uses `abs(residual) < _RESIDUAL_TOLERANCE`
- Zero occurrences of `residual == 0.0` or `residual == 0` remain in the file

If any of these are missing → add them. Otherwise → do nothing.

---

### FIX-2 · `src/repair_engine.py` — Debug print → logging

**Read the file. Confirm:**
- `import logging` and `logger = logging.getLogger(__name__)` are at the top
- Zero `print(` calls remain anywhere in the file
- All debug output uses `logger.debug(...)`

If any `print(` remains → replace with `logger.debug(...)`. Otherwise → do nothing.

---

### FIX-3 · `src/field_extractor.py` — Auditor loop break

**Read the file. Confirm the auditor extraction block has this structure:**

```python
found_opinion = False
for block in text_blocks:
    if found_opinion:
        break                     # ← outer loop guard
    for kw in auditor_keywords:
        ...
        if opinion_value is not None:
            results["Auditor's Opinion"] = FieldValue(...)
            found_opinion = True
            break                 # ← inner loop break
```

If `found_opinion` flag or outer `break` is missing → add them. Otherwise → do nothing.

---

### FIX-4 · `src/field_extractor.py` — No inline import in loop

**Read the file. Confirm:**
- `from .value_parser import parse_numeric` (or any import) is NOT inside a function body or loop

If an inline import exists → move it to the top-level imports. Otherwise → do nothing.

---

### FIX-5 · `src/main.py` — Single preprocessing pass with cache

**Read the file. Confirm `process_file` does this:**

```python
processed_images = {
    page_idx: preprocess_image(pil_img)
    for page_idx, pil_img in doc.pages
}
# Then iterates processed_images — does NOT call preprocess_image again
for page_idx, processed_img in processed_images.items():
    page_tokens = ocr_engine.recognize_page(processed_img, page_idx)
```

And that `apply_math_repairs(field_values, processed_images, ...)` receives the same dict.

If preprocessing is called more than once per page → refactor to the cache pattern above. Otherwise → do nothing.

---

### FIX-6 · `src/models.py` — `field_label` field on FieldValue

**Read the file. Confirm:**
- `FieldValue` dataclass has `field_label: Optional[str] = None`

If missing → add it as the last field with default `None`. Otherwise → do nothing.

---

### FIX-7 · `src/exporter.py` — `field_label` as first output key

**Read the file. Confirm `field_value_to_dict` returns a dict where `"field_label"` is the first key.**

```python
return {
    "field_label": f.field_label,   # must be FIRST
    "value": f.value,
    ...
}
```

If `field_label` is missing or not first → fix the dict. Otherwise → do nothing.

---

### FIX-8 · `src/field_extractor.py` — `field_label` populated on construction

**Read the file. Confirm:**
- Every `FieldValue(...)` construction for numeric fields sets `field_label=row.description`
- The Auditor's Opinion `FieldValue(...)` sets `field_label="Auditor's Opinion"`

If any construction is missing `field_label` → add it. Otherwise → do nothing.

---

### FIX-9 · `src/field_config.yaml` — Expanded keywords for 3 weak fields

**Read the file. Confirm these keywords exist:**

| Field | Must include |
|---|---|
| `Plant and Equipment` | `"Property, plant and equipment"`, `"Tangible fixed assets"`, `"Right-of-use assets"` |
| `Trade Receivables` | `"Trade and other receivables"`, `"Receivables from customers"`, `"Net trade receivables"` |
| `Auditor's Opinion` | `"Qualified opinion"`, `"Disclaimer of opinion"`, `"Independent Auditor's Report"` |

If any of these are missing → append them to the existing keyword list for that field. Do not remove any existing keywords. Otherwise → do nothing.

---

## Part 2 — Re-Run Pipeline on AA_SAMPLE3.pdf

Once all 9 verifications pass, re-run the pipeline.

### Command

```bash
cd /path/to/ocr_project
python -m src.main path/to/AA_SAMPLE3.pdf --optimize --output artifacts/AA_SAMPLE3_output.json
```

Replace the paths with wherever the project and sample PDFs actually live on disk.
The `--optimize` flag enables the Combinatorial Inverse Search (Phase 3 of repair_engine).

### Expected output shape per field

```json
{
  "Trade Receivables": {
    "field_label": "Trade and other receivables",
    "value": 74677.0,
    "raw_text": "74,677",
    "evidence": "74 677",
    "page": 3,
    "bbox": [120, 450, 280, 470],
    "valid": true,
    "reason": null
  }
}
```

`field_label` must be the exact string from the document row, not the config key name.

---

## Part 3 — Verify the 3 Problem Fields in Output

After the re-run, open `artifacts/AA_SAMPLE3_output.json` and check:

| Field | Pass condition |
|---|---|
| `Plant and Equipment` | `value` is a non-null number; `field_label` matches the actual row text in the PDF |
| `Trade Receivables` | `value` is non-null and is NOT `113718.0` (that was the wrong row from V5) |
| `Auditor's Opinion` | `value` is one of: `"Qualified"`, `"Unqualified"`, `"Adverse"`, `"Disclaimer"` |

If any field still fails:
1. Re-run with debug logging to see which rows were matched: add `import logging; logging.basicConfig(level=logging.DEBUG)` temporarily
2. Find the exact label text in the PDF for the failing field
3. Add that exact text as a new keyword in `field_config.yaml`
4. Re-run until the field passes

---

## Part 4 — Commit

Use this commit message:

```
fix(round1): verify all trainer feedback fixes; regenerate Sample3 output

All 9 Round 1 fixes confirmed present on branch.
Regenerated artifacts/AA_SAMPLE3_output.json with --optimize flag.
field_label now first key in all field outputs.
Trade Receivables, Plant and Equipment, Auditor's Opinion verified non-null.
```

Push to `fix/trainer-feedback-round1`.

---

## Definition of Done

- [ ] All 9 FIX verifications above confirmed (read files, checked code)
- [ ] `python -m src.main AA_SAMPLE3.pdf --optimize` runs without error or exception
- [ ] `artifacts/AA_SAMPLE3_output.json` exists and is valid JSON
- [ ] `Plant and Equipment` → `value` is non-null
- [ ] `Trade Receivables` → `value` is non-null and not the wrong row value
- [ ] `Auditor's Opinion` → `value` is one of the four valid opinion strings
- [ ] Every field in output has `field_label` as the first key
- [ ] Commit pushed to `fix/trainer-feedback-round1`
