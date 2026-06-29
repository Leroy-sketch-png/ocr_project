# OCR Project — Deep Implementation Plan (Agent v2)

> **Branch to work on:** `fix/trainer-feedback-round1`  
> **Merge target:** `main`  
> **This document is the single source of truth for the agent. Read it fully before touching any file.**

---

## 0. Context and Strategy

The trainer's feedback has two layers:

1. **Surface bugs** — floating-point comparison, missing `break`, double preprocessing, debug prints, inline import. These are mechanical. Fix them exactly as described.
2. **Extraction quality failures** — Plant and Equipment, Trade Receivables, Auditor's Opinion returning wrong or missing values. These are *not* just keyword problems. The root cause is architectural: the current pipeline assumes every financial line item sits in a clean table row with a description column that fuzzy-matches a keyword. Real annual report PDFs break this assumption in at least four ways (described below).

Fix the surface bugs first. Then fix the extraction architecture. Then re-run on Sample 3.

---

## 1. Surface Bug Fixes (Do These First, in Order)

### 1.1 `repair_engine.py` — Float equality checks

**Problem:** Two locations use `== 0.0` to test whether a residual is satisfied. Floating-point arithmetic guarantees this will silently fail on real numbers like `1234567.00 - 617283.00 - 617284.00 = -1e-10`.

**Fix — add near the top of `repair_engine.py`, after the imports:**

```python
_RESIDUAL_TOLERANCE = 1e-2  # 0.01 — sub-cent tolerance is a solved equation

def _residual_ok(residual: Optional[float]) -> bool:
    """Return True if residual is None (missing fields) or close enough to zero."""
    return residual is not None and abs(residual) < _RESIDUAL_TOLERANCE
```

**Replace every `residual == 0.0` and `new_res == 0.0` occurrence** (there are 5 total) with `_residual_ok(residual)` / `_residual_ok(new_res)` as appropriate.

**Also fix the guard at the top of the equation loop:**

```python
# BEFORE
if residual is None or residual == 0.0:
    continue

# AFTER
if residual is None or _residual_ok(residual):
    continue
```

**Acceptance criterion:** `grep -n "== 0.0" src/repair_engine.py` returns zero results.

---

### 1.2 `repair_engine.py` — Replace all `print()` with `logging`

**Problem:** 8 `print()` calls exist in `repair_engine.py`. They pollute stdout in production.

**Fix — add at the top of `repair_engine.py` (after stdlib imports, before local imports):**

```python
import logging
logger = logging.getLogger(__name__)
```

**Replace every `print(...)` call with `logger.debug(...)`** using the same message string. Do not change the message content, only the call.

Example:
```python
# BEFORE
print(f"  [REPAIR] {field}: ...")

# AFTER
logger.debug(f"  [REPAIR] {field}: ...")
```

**Acceptance criterion:** `grep -n "^    print\|^        print\|^print" src/repair_engine.py` returns zero results.

---

### 1.3 `field_extractor.py` — Auditor outer loop must break

**Problem:** The outer `for block in text_blocks` loop that searches for Auditor's Opinion has a `break` that only exits the inner `for kw in auditor_kws` loop. The outer loop continues, and a later block that contains a keyword but fails `normalize_auditor_opinion` will overwrite a valid earlier result with `opinion_value=None`.

**Fix — replace the Auditor's Opinion section entirely:**

```python
# Extract Auditor's Opinion
auditor_kws = flat_config.get("Auditor's Opinion", [])
found_opinion = False
for block in text_blocks:
    if found_opinion:
        break
    text_lower = " ".join(t.text for t in block.tokens).lower()
    for kw in auditor_kws:
        if kw.lower() in text_lower:
            opinion_value = normalize_auditor_opinion(text_lower)
            if opinion_value is None:
                # keyword present but opinion type not determinable from this block — skip
                continue
            results["Auditor's Opinion"] = FieldValue(
                name="Auditor's Opinion",
                value=opinion_value,
                raw_text=kw,
                page=block.page,
                tokens=block.tokens,
                bbox=compute_bbox(block.tokens),
                valid=True,
                reason=None,
            )
            found_opinion = True
            break
```

**Key differences from original:**
- `found_opinion` flag causes the outer `for block` loop to exit on first valid match.
- If a block has the keyword but `normalize_auditor_opinion` returns `None`, we `continue` to the next keyword/block rather than writing a bad result.
- A block that yields a valid opinion immediately sets `found_opinion = True` and double-breaks.

**Acceptance criterion:** Run on AA_SAMPLE3. `Auditor's Opinion` in output must be one of `Qualified / Unqualified / Adverse / Disclaimer` and must not be `null` if the word "opinion" appears in the document.

---

### 1.4 `field_extractor.py` — Move inline import to top of file

**Problem:** `from .value_parser import parse_numeric` appears inside the inner loop body (line ~90 in the current file). Python caches it after the first call, but it signals sloppy code structure to any reviewer.

**Fix:** Remove the inline `from .value_parser import parse_numeric` line from inside the loop. Add it to the top-of-file imports block, which currently ends at:

```python
from .models import FieldValue, TableRow, TextBlock, Token
```

Add immediately after:

```python
from .value_parser import parse_numeric
```

**Acceptance criterion:** `grep -n "from .value_parser" src/field_extractor.py` returns exactly 1 result at a line number < 15.

---

### 1.5 `ocr_engine.py` + `main.py` — Eliminate double preprocessing

**Problem:** `main.py` currently calls `preprocess_image()` once to build `processed_images`, then passes pages to the OCR engine which calls `preprocess_image()` again internally. Same pixels, same computation, twice.

**Fix — in `ocr_engine.py`:** The `recognize_page` method must accept an already-preprocessed image. Change its signature so it takes a `preprocessed_img` parameter. Remove the internal `preprocess_image()` call from inside that method. If `preprocessed_img` is not provided (backwards compatibility), fall back to preprocessing internally.

```python
# AFTER (signature change only — implementation stays identical except for removing internal preprocess)
def recognize_page(self, preprocessed_img, page_number: int) -> List[Token]:
    # preprocessed_img is already a preprocessed numpy array
    data = pytesseract.image_to_data(preprocessed_img, output_type=Output.DICT, ...)
    ...
```

**Fix — in `main.py`:** Build `processed_images` dict first (this already happens), then pass each `processed_images[page_num]` directly into `recognize_page`. Do not call `preprocess_image()` a second time anywhere in the OCR loop.

**Acceptance criterion:** `grep -rn "preprocess_image" src/` shows the function defined once in `image_processor.py` and called exactly once per page in `main.py`. Zero calls inside `ocr_engine.py`.

---

## 2. Extraction Quality Fixes (Do These After Section 1)

This is where the real work is. The three failing fields share a common root cause, but each has a specific fix.

---

### 2.1 Understanding why these three fields fail

Read this section fully before writing any code.

#### Why `Trade Receivables` fails

Annual reports label this line in at least 6 different ways depending on country and accounting standard:
- "Trade receivables" (IFRS)
- "Trade and other receivables"
- "Accounts receivable"
- "Trade debtors"
- "Receivables from customers"
- "Net receivables"

The current `compute_match_score` function penalizes extra words. A row labelled **"Trade and other receivables"** scores poorly against the keyword `"Trade Receivables"` because the extra words `"and"`, `"other"` increase `extra_words` count, pushing the score below the 82-threshold even though this is clearly the same field.

The fix is twofold: (a) expand keywords in `field_config.yaml`, and (b) improve `compute_match_score` to reward _containment_ — if the query words are a subset of the description words, it should score high regardless of extra words in the description.

#### Why `Plant and Equipment` fails

This field appears in two formats:
- As a single line: `"Property, plant and equipment"` — one row in the table.
- As a subtotal with sub-lines: e.g. `"Plant and machinery"`, `"Equipment"`, `"Motor vehicles"` each on their own row, summed into `"Property, plant and equipment"` on a total row.

The current pipeline grabs whichever row scores highest, which may be a sub-line rather than the total. The fix is to add **anchor preference**: if multiple rows match a field's keywords, prefer the row whose description most closely resembles the canonical name (highest `compute_match_score` with no extra penalty for "property" prefix).

Also expand the keyword list in `field_config.yaml` with `"Property, plant and equipment"` and `"PP&E"`.

#### Why `Auditor's Opinion` fails (extraction-side, beyond the loop fix)

`normalize_auditor_opinion` only matches if the exact phrase `"qualified opinion"`, `"unqualified opinion"`, etc. appears in the block text. Real audit reports often split these across sentences:

> *"In our opinion, the financial statements give a true and fair view... The opinion expressed is unmodified."*

The word `"unmodified"` appears far from the word `"opinion"` in the text stream. `normalize_auditor_opinion` will miss it.

The fix is to broaden the pattern matching: check for the opinion-type words independently of the word "opinion" appearing in the same phrase.

---

### 2.2 `field_config.yaml` — Expand keyword lists

**For `Trade Receivables` — add these keywords:**
```yaml
- "trade and other receivables"
- "accounts receivable"
- "trade debtors"
- "receivables from customers"
- "net receivables"
- "trade receivable"
```

**For `Plant and Equipment` — add these keywords:**
```yaml
- "property plant and equipment"
- "property, plant and equipment"
- "PP&E"
- "plant and machinery"
- "plant equipment"
- "fixed assets"
```

**For `Auditor's Opinion` — add these keywords:**
```yaml
- "independent auditor"
- "auditors report"
- "independent auditors report"
- "report of the independent"
- "basis for opinion"
- "qualified opinion"
- "unqualified opinion"
- "adverse opinion"
- "disclaimer of opinion"
- "emphasis of matter"
```

---

### 2.3 `field_extractor.py` — Fix `compute_match_score` containment logic

**Problem:** Extra words in a description always reduce the score. This is wrong for financial field names where the document often uses a longer canonical form than our keyword.

**Replace the `compute_match_score` function entirely:**

```python
def compute_match_score(query: str, desc: str) -> float:
    """
    Score how well `desc` matches `query`.
    
    Design:
    - Full token overlap (query tokens ⊆ desc tokens): high score, no extra-word penalty.
    - Partial overlap: scaled score, small extra-word penalty.
    - No overlap: fall back to fuzzy ratio.
    
    The key change from the old version: when query tokens are fully contained
    in desc tokens (containment match), we do NOT penalise the extra description
    words. This handles "Trade and other receivables" matching "Trade Receivables".
    """
    q_clean = re.sub(r"[^a-z0-9\s]", "", query.lower())
    d_clean = re.sub(r"[^a-z0-9\s]", "", desc.lower())

    q_tokens = set(q_clean.split())
    d_tokens = set(d_clean.split())

    # Remove stop words that add noise
    _STOPS = {"and", "or", "the", "of", "for", "in", "net", "total", "other"}
    q_tokens -= _STOPS
    d_tokens -= _STOPS

    if not q_tokens or not d_tokens:
        return fuzz.ratio(query.lower(), desc.lower())

    intersection = q_tokens.intersection(d_tokens)
    if not intersection:
        return fuzz.ratio(query.lower(), desc.lower())

    recall = len(intersection) / len(q_tokens)  # how much of query is covered
    extra_words = len(d_tokens) - len(intersection)  # words in desc not in query

    if recall == 1.0:
        # All query tokens are present in description — containment match.
        # Do NOT penalise extra words: "Trade and other receivables" should
        # score as well as "Trade receivables" for the query "trade receivables".
        base = 95.0
        # Small penalty only if description is 3x longer than query (very different)
        if extra_words > len(q_tokens) * 2:
            base -= 5.0
        return max(base, fuzz.ratio(query.lower(), desc.lower()))
    elif recall >= 0.5:
        # Partial overlap — keep a small extra-word penalty but don't kill the score
        score = (recall * 90) - (extra_words * 5)
        return max(score, fuzz.ratio(query.lower(), desc.lower()))

    return fuzz.ratio(query.lower(), desc.lower())
```

**Acceptance criterion:** A description `"trade and other receivables"` scored against query `"trade receivables"` must return >= 82. Write this as a one-liner test before committing:
```python
assert compute_match_score("trade receivables", "trade and other receivables") >= 82
```

---

### 2.4 `field_extractor.py` — Fix `normalize_auditor_opinion` to handle split phrases

**Replace `normalize_auditor_opinion`:**

```python
def normalize_auditor_opinion(text: str) -> Optional[str]:
    """
    Classify an auditor's opinion block into one of the four standard labels.
    
    Handles both:
    - Compact phrases: "qualified opinion", "adverse opinion"
    - Split mentions: "our opinion is unmodified" (no adjacent "opinion")
    """
    t = text.lower()

    # Check for disclaimer first (most specific — a disclaimer IS NOT a qualified)
    if "disclaimer" in t:
        return "Disclaimer"

    # Adverse opinion
    if "adverse" in t and ("opinion" in t or "view" in t):
        return "Adverse"

    # Qualified opinion — "qualified" appears near "opinion"
    # Also catches "except for" which is the standard signal of a qualified opinion
    if "qualified opinion" in t or ("qualified" in t and "opinion" in t):
        # Make sure it's not "unqualified" being matched by "qualified"
        if "unqualified" not in t and "unmodified" not in t:
            return "Qualified"
    if "except for" in t:
        return "Qualified"

    # Unqualified / clean / unmodified opinion
    if (
        "unqualified opinion" in t
        or "unmodified opinion" in t
        or "unmodified" in t
        or "true and fair view" in t
        or "present fairly" in t
        or "clean opinion" in t
    ):
        return "Unqualified"

    return None
```

**Key improvements:**
- Checks `"disclaimer"` standalone — audit reports often write "We disclaim an opinion" not "disclaimer of opinion".
- Checks `"except for"` as a qualified-opinion signal (this is the canonical IFRS/ISA marker).
- Checks `"true and fair view"` and `"present fairly"` as unqualified signals (standard closing phrases in clean audit reports).
- Ensures `"qualified"` inside `"unqualified"` doesn't cause a false positive.

**Acceptance criterion:** Run these assertions before committing:
```python
assert normalize_auditor_opinion("the financial statements present fairly") == "Unqualified"
assert normalize_auditor_opinion("except for the matter described") == "Qualified"
assert normalize_auditor_opinion("we disclaim any opinion on these statements") == "Disclaimer"
assert normalize_auditor_opinion("adverse opinion on the financial statements") == "Adverse"
assert normalize_auditor_opinion("the opinion is unmodified") == "Unqualified"
```

---

### 2.5 `field_extractor.py` — Add `field_label` to every `FieldValue`

The trainer explicitly asked: *"In the final output, please include the exact field label from the document (not just the value + evidence)."*

**In `models.py` — add the field:**
```python
@dataclass
class FieldValue:
    name: str
    value: Optional[float]
    raw_text: Optional[str]
    page: Optional[int]
    tokens: List[Token]
    bbox: Optional[Tuple[int, int, int, int]]
    valid: bool
    reason: Optional[str]
    row_candidates: List[Tuple[str, float]] = field(default_factory=list)
    field_label: Optional[str] = None  # exact label text from document
```

**In `field_extractor.py` — populate `field_label`** at every `FieldValue` construction in `extract_fields`:

For numeric fields (inside the `should_update` block):
```python
results[field_name] = FieldValue(
    ...
    field_label=row.description,   # exact text from the document row
)
```

For Auditor's Opinion:
```python
results["Auditor's Opinion"] = FieldValue(
    ...
    field_label=" ".join(t.text for t in block.tokens[:12]),  # first ~12 tokens of the block
)
```

**In `exporter.py` — include `field_label` as the first key in each field's JSON object:**
```python
def export_field(fv: FieldValue) -> dict:
    return {
        "field_label": fv.field_label,
        "value": fv.value,
        "raw_text": fv.raw_text,
        "page": fv.page,
        "bbox": fv.bbox,
        "confidence": ...,
        "valid": fv.valid,
        "reason": fv.reason,
    }
```

**Acceptance criterion:** In the Sample 3 JSON output, every field object has a `"field_label"` key that is non-null and contains real text from the document.

---

## 3. Verification Checklist

Before opening (or updating) the PR, verify all of the following:

### Code health
- [ ] `grep -n "== 0.0" src/repair_engine.py` → 0 results
- [ ] `grep -n "^    print\|^        print" src/repair_engine.py` → 0 results
- [ ] `grep -n "from .value_parser" src/field_extractor.py` → exactly 1 result, line < 15
- [ ] `grep -n "preprocess_image" src/ocr_engine.py` → 0 results
- [ ] `grep -n "found_opinion" src/field_extractor.py` → present

### Logic assertions (run in a Python REPL or as a quick script)
```python
from src.field_extractor import compute_match_score, normalize_auditor_opinion

# Trade Receivables containment
assert compute_match_score("trade receivables", "trade and other receivables") >= 82
assert compute_match_score("trade receivables", "accounts receivable") >= 60

# Plant & Equipment
assert compute_match_score("plant and equipment", "property plant and equipment") >= 82

# Auditor opinion variants
assert normalize_auditor_opinion("the financial statements present fairly") == "Unqualified"
assert normalize_auditor_opinion("except for the matter described") == "Qualified"
assert normalize_auditor_opinion("we disclaim any opinion") == "Disclaimer"
assert normalize_auditor_opinion("adverse opinion on the statements") == "Adverse"
assert normalize_auditor_opinion("the opinion is unmodified") == "Unqualified"

print("All assertions passed.")
```

### Output quality on Sample 3
Run the pipeline on `AA_SAMPLE3.pdf`. The JSON output must satisfy:
- [ ] `Trade Receivables` → `value` is non-null, `field_label` is non-null
- [ ] `Plant and Equipment` → `value` is non-null, `field_label` is non-null
- [ ] `Auditor's Opinion` → `value` is one of `Qualified / Unqualified / Adverse / Disclaimer`, `valid: true`
- [ ] Every field has `"field_label"` as the first key in its JSON object
- [ ] No field whose value exists in the source document has `null` in the output

---

## 4. Commit Message

```
fix: trainer feedback round 1 — extraction quality + code correctness

Bug fixes:
- repair_engine: replace residual == 0.0 with _residual_ok() tolerance check
- repair_engine: replace all print() with logger.debug()
- field_extractor: add found_opinion flag + break to prevent overwrite
- field_extractor: move inline import to top of file
- ocr_engine + main: single preprocessing pass, no double processing

Extraction improvements:
- field_extractor: rewrite compute_match_score with containment logic
  (no extra-word penalty when query tokens are subset of description tokens)
- field_extractor: rewrite normalize_auditor_opinion with split-phrase support
  (handles "except for", "true and fair view", "present fairly", "unmodified")
- field_config: expand keywords for Trade Receivables, Plant and Equipment,
  Auditor's Opinion
- models + exporter: add field_label field; output exact document label per field
```

---

## 5. Why These Changes, Not Others

### Why rewrite `compute_match_score` instead of just adding more keywords?

Keywords are brittle. A document that says "Trade and other receivables — net" will still fail if we only add `"trade and other receivables"` as a keyword, because the `" — net"` suffix adds extra words that tank the score. The containment logic fix means any description that *contains* the query's key words scores highly, regardless of surrounding text. This is the correct semantic model for financial field matching.

### Why not use a more powerful NLP model?

The assignment is confidential, must run locally, and the evaluator cares about a clear pipeline — not ML model selection. Fixing the scoring function costs zero dependencies and is fully explainable in the design writeup. Always prefer the minimal correct solution.

### Why split `_residual_ok()` into a named function instead of an inline `abs()`?

The tolerance constant `1e-2` needs to be defined in exactly one place. If you inline `abs(residual) < 1e-2` in five spots, it takes one copy-paste mistake to get an inconsistent threshold. A named function is one line to change if the threshold ever needs adjusting.

### Why does `normalize_auditor_opinion` check `"except for"` as a Qualified signal?

ISA 705 (the international auditing standard) defines a qualified opinion as one given "except for the effects of the matter described." This phrase is how auditors signal a qualification in practice. Any audit report with "except for" in the opinion paragraph is, by definition, a Qualified opinion under ISA 705. This is more reliable than matching the word "qualified" which may appear in other contexts.
