# IMPLEMENTATION PLAN V4 — Beyond the 7 Fixes
## Three Architectural Additions That Give This Commercial Legs

**Branch:** `V3`  
**Prerequisite:** `IMPLEMENTATION_PLAN_V3.md` Steps 1–7 must be complete and verified first.  
**Do not start this plan until V3 is done and all 45 currently-correct fields still pass.**

This plan adds zero sample-specific logic. Every change here must hold on a
document you have never seen from a company in any country.

---

## REALITY CHECK

Three things will kill this pipeline on real client documents that are NOT
failing on these 3 samples yet:

1. A document that says "amounts expressed in thousands" — your extracted
   values will be 1000x wrong. Silently. No error raised.
2. A document where the balance sheet is on page 47 of a 200-page report
   and the notes pages contain the same field labels as keywords — you will
   extract from the wrong page.
3. A document with slightly different label phrasing not in your config —
   your fixed keyword list will miss it.

All three have zero lines of fix in V3. This plan addresses all three.
None of them require more compute. All are pure logic.

---

## ADDITION A — Unit Scale Detection
### New file: `src/unit_detector.py`

**The problem:**  
Financial statements declare their scale somewhere: "(in thousands)",
"expressed in millions of USD", "'000", "ribuan". Your pipeline extracts
the raw number and treats it as face value. A company with revenue of
40,580 thousand is NOT the same as revenue of 40,580.

**Why this is not in V3:**  
It does not affect the 3 samples we can verify against. But it will
silently break every document where the scale is non-unit.

**Implementation:**

```python
# src/unit_detector.py
import re
from typing import List
from .models import Token

# Patterns and their multipliers.
# Order matters — more specific patterns first.
_SCALE_PATTERNS = [
    # Billions
    (re.compile(r"in\s+billions?", re.I), 1_000_000_000),
    (re.compile(r"'000[,\s]*000", re.I), 1_000_000),  # '000,000
    (re.compile(r"expressed\s+in\s+millions?", re.I), 1_000_000),
    (re.compile(r"\(in\s+millions?\)", re.I), 1_000_000),
    (re.compile(r"in\s+millions?", re.I), 1_000_000),
    # Thousands
    (re.compile(r"expressed\s+in\s+thousands?", re.I), 1_000),
    (re.compile(r"\(in\s+thousands?\)", re.I), 1_000),
    (re.compile(r"in\s+thousands?", re.I), 1_000),
    (re.compile(r"'000\b", re.I), 1_000),
    (re.compile(r"ribuan", re.I), 1_000),     # Indonesian
    (re.compile(r"juta", re.I), 1_000_000),  # Indonesian millions
    (re.compile(r"tusinde", re.I), 1_000),   # Danish thousands
]

def detect_unit_scale(tokens: List[Token], header_page_count: int = 3) -> float:
    """
    Scan the first `header_page_count` pages for a scale declaration.
    Returns a multiplier (1.0 if no scale declaration found).
    
    Strategy: scan headers/footers first (y < 200px or y > page_height - 200px),
    then full text of early pages. First match wins.
    """
    # First pass: page headers and footers (y position heuristic)
    header_footer_text = " ".join(
        t.text for t in tokens
        if t.page <= header_page_count and (t.bbox[1] < 200 or t.bbox[1] > 2400)
    )
    for pattern, multiplier in _SCALE_PATTERNS:
        if pattern.search(header_footer_text):
            return float(multiplier)

    # Second pass: full text of first N pages
    early_text = " ".join(
        t.text for t in tokens
        if t.page <= header_page_count
    )
    for pattern, multiplier in _SCALE_PATTERNS:
        if pattern.search(early_text):
            return float(multiplier)

    return 1.0
```

**Wire it into `main.py`:**

After `all_tokens` is built and before `extract_fields`:
```python
from .unit_detector import detect_unit_scale

unit_scale = detect_unit_scale(all_tokens)
```

After `parse_numeric_fields`:
```python
if unit_scale != 1.0:
    for fv in field_values.values():
        if fv.value is not None and isinstance(fv.value, float):
            fv.value *= unit_scale
            fv.reason = (fv.reason or "") + f" [scale x{int(unit_scale)}]"
```

Apply BEFORE the repair engine runs, so accounting equations still hold
after scaling.

**What NOT to do:**
- Do not apply scaling to `"Auditor's Opinion"` (it's a string, skip it).
- Do not hardcode a multiplier per sample. Detect it every time.
- Do not apply to `row_candidates` individually — the repair engine uses
  them for ratio checks, not absolute values, so scaling all fields
  uniformly is sufficient.

---

## ADDITION B — Page Classifier (keyword-density, no ML)
### New file: `src/page_classifier.py`

**The problem:**  
Your extractor currently runs `build_table_rows` on ALL tokens from ALL pages.
A 150-page annual report has ~140 pages of notes, narrative, legal text — all
of which can contain the same keywords as your field config. The Plant &
Equipment = 3.0 failure is one symptom. There are more waiting in longer documents.

**This is NOT about speed.** It's about reducing the search space so the
highest-scoring row for a field is the one on the balance sheet, not a
note on page 74.

**Implementation — keyword-density scoring per page:**

```python
# src/page_classifier.py
from typing import Dict, List
from .models import Token

# Field section → signature keywords that appear on that page type.
# These are section-level signals, not field labels.
_SECTION_SIGNATURES: Dict[str, List[str]] = {
    "balance_sheet": [
        "total assets", "total liabilities", "total equity",
        "current assets", "non-current assets", "shareholders equity",
        "statement of financial position",
    ],
    "income_statement": [
        "revenue", "gross profit", "operating profit", "profit before tax",
        "net profit", "profit for the year", "cost of sales",
        "statement of comprehensive income", "income statement",
        "statement of profit or loss",
    ],
    "auditor_report": [
        "independent auditor", "we have audited", "in our opinion",
        "basis for opinion", "auditor's responsibility", "those charged with governance",
    ],
    "notes": [
        "note ", "notes to", "accounting policies", "significant accounting",
        "(note", "see note",
    ],
}

# Which section each field should be extracted from.
# Fields not listed here are extracted from ALL pages (fallback).
FIELD_TO_SECTION = {
    "Revenue": "income_statement",
    "Gross Profit/Loss": "income_statement",
    "Operating Profit/Loss": "income_statement",
    "Profit/Loss Before Tax": "income_statement",
    "Net Profit/Loss": "income_statement",
    "Cost of Sales": "income_statement",
    "Total Assets": "balance_sheet",
    "Current Assets": "balance_sheet",
    "Non-Current Assets": "balance_sheet",
    "Trade Receivables": "balance_sheet",
    "Cash and Cash Equivalents": "balance_sheet",
    "Plant and Equipment": "balance_sheet",
    "Total Liabilities": "balance_sheet",
    "Current Liabilities": "balance_sheet",
    "Non-Current Liabilities": "balance_sheet",
    "Paid Up Capital": "balance_sheet",
    "Retained Earnings": "balance_sheet",
    "Total Equity": "balance_sheet",
    "Auditor's Opinion": "auditor_report",
}

def classify_pages(tokens: List[Token]) -> Dict[int, str]:
    """
    Score each page against section signatures.
    Returns {page_number: section_label} for the dominant section.
    Pages with no dominant section are labelled "other".
    """
    from collections import defaultdict
    page_texts: Dict[int, str] = defaultdict(str)
    for t in tokens:
        page_texts[t.page] += " " + t.text.lower()

    page_labels: Dict[int, str] = {}
    for page, text in page_texts.items():
        scores = {}
        for section, sigs in _SECTION_SIGNATURES.items():
            score = sum(1 for sig in sigs if sig in text)
            scores[section] = score
        best = max(scores, key=scores.get)
        page_labels[page] = best if scores[best] >= 2 else "other"
    return page_labels


def filter_tokens_for_field(
    field_name: str,
    tokens: List[Token],
    page_labels: Dict[int, str],
) -> List[Token]:
    """
    Return only the tokens from pages relevant to the given field.
    Falls back to ALL tokens if the required section has no pages classified.
    """
    required_section = FIELD_TO_SECTION.get(field_name)
    if required_section is None:
        return tokens  # no restriction

    allowed_pages = {
        p for p, label in page_labels.items()
        if label == required_section
    }

    # Safety: if classifier found no pages for this section, use all pages.
    # Never return empty — that would silently null every field.
    if not allowed_pages:
        return tokens

    return [t for t in tokens if t.page in allowed_pages]
```

**Wire it into `field_extractor.py`:**

This requires a small signature change to `extract_fields` — it needs to
receive `page_labels` and filter `table_rows` per field:

```python
# In extract_fields, inside the field extraction loop:
for field_name, keywords in flat_config.items():
    if field_name == "Auditor's Opinion":
        continue

    # Filter rows to only those on pages relevant for this field
    required_section = FIELD_TO_SECTION.get(field_name)
    if required_section and page_labels:
        allowed_pages = {p for p, lbl in page_labels.items() if lbl == required_section}
        if allowed_pages:  # only restrict if we found relevant pages
            candidate_rows = [r for r in table_rows if r.page in allowed_pages]
        else:
            candidate_rows = table_rows
    else:
        candidate_rows = table_rows

    for row in candidate_rows:
        # ... rest of existing loop unchanged
```

Call `classify_pages` in `main.py` after `build_text_blocks`:
```python
from .page_classifier import classify_pages
page_labels = classify_pages(all_tokens)
```
Pass `page_labels` into `extract_fields`.

**Critical safety rule:** The classifier uses a threshold of `>= 2` matching
signatures. If a page scores 1 or 0, it's labelled "other", not misclassified.
The fallback to ALL tokens when no pages match a section means you can never
lose a field due to misclassification — you just lose the noise reduction.

---

## ADDITION C — BM25 scoring in `compute_match_score`
### Modify: `src/field_extractor.py`

**The problem:**  
Your current `compute_match_score` is a hand-rolled token overlap function.
It works but has no IDF (inverse document frequency) weighting — all words
count equally. "Total" in "Total Assets" counts the same as "Assets", even
though "total" appears on every single row and is therefore low-signal.

**This is NOT about adding compute.** BM25 is a 1994 algorithm. It runs in
microseconds. It's better-grounded than what you have.

**However:** rank_bm25 requires building a corpus first (all row descriptions),
then querying against it. The current architecture calls `compute_match_score`
row-by-row in a loop. Refactoring to full BM25 requires restructuring the
extraction loop. That's a non-trivial refactor.

**Pragmatic version — augment, don't replace:**

Add IDF-like weighting to the existing function. Words that appear in EVERY
row (like "total", "net") should count less than words unique to a few rows
(like "depreciation", "goodwill"). You don't need to build a full corpus.
You can pre-compute a word frequency table from `field_config.yaml`'s
own keyword list.

```python
# Pre-compute word frequency across all config keywords at load time
# (call this once when loading config)
def build_keyword_idf(flat_config: dict) -> dict:
    from collections import Counter
    import math
    all_words = []
    for keywords in flat_config.values():
        for kw in keywords:
            words = re.sub(r"[^a-z0-9\s]", "", kw.lower()).split()
            all_words.extend(words)
    freq = Counter(all_words)
    total_docs = len(flat_config)  # number of fields as proxy for doc count
    idf = {}
    for word, count in freq.items():
        idf[word] = math.log((total_docs + 1) / (count + 1)) + 1.0
    return idf
```

Then in `compute_match_score`, weight the intersection by IDF:
```python
def compute_match_score(query, desc, idf=None):
    # ... existing token setup ...
    if idf and recall == 1.0:
        weighted_score = sum(idf.get(w, 1.0) for w in intersection)
        base = 95.0 * (weighted_score / sum(idf.get(w, 1.0) for w in q_tokens))
        # ... rest unchanged
```

**Honest assessment:** This is the least impactful of the three additions.
Do it last, only if the other two are stable. The keyword expansion in V3
Step 1 buys more recall than IDF weighting will.

---

## IMPLEMENTATION ORDER FOR V4

**Do in this exact order. Verify after each step.**

| Step | What | Risk if wrong |
|---|---|---|
| V4-A | `unit_detector.py` + wire into `main.py` | All field values multiplied by wrong factor — easy to spot |
| V4-B | `page_classifier.py` + wire into `field_extractor.py` | Fields go null if classifier miscategorises — fallback prevents this |
| V4-C | IDF weighting in `compute_match_score` | Score ordering changes — rerun eval immediately to check |

After each step: run against all 3 samples and confirm no regression.

---

## WHAT THESE ADDITIONS ACTUALLY UNLOCK

| Without | With |
|---|---|
| 1000x wrong value on any "in thousands" document | Correct value, tagged with `[scale x1000]` in reason |
| Notes page false matches as document size grows | Notes pages excluded from field search |
| Generic labels scoring the same regardless of uniqueness | Rare specific labels score higher than generic ones |
| Pipeline breaks silently on real client docs | Pipeline degrades gracefully with fallbacks |

These are the difference between a student project and something you can
charge money for.
