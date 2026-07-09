# OCR Financial Statement Extraction Pipeline — Handoff

## Evaluation: 200/200 (100%)

| Sample   | Type        | Fields | Years | Notes |
|----------|-------------|--------|-------|-------|
| SAMPLE1  | Synthetic   | 19     | 1-2   | Single-year IS, P&L with losses |
| SAMPLE2  | Synthetic   | 20     | 2     | Services-only, no CoS/GP |
| SAMPLE3  | Synthetic   | 21     | 2-3   | Mixed periods, qualified audit |
| KO       | Real 10-K   | 23     | 2-3   | Coca-Cola FY2024, ~190pp |
| AAPL     | Real 10-K   | 21     | 2-3   | Apple FY2023, ~50pp |

All 7 accounting identities pass for every populated year across all 5 samples (0 errors).

## Recent commits (top to bottom)

```
974e024 fix: NCL tiebreak prefers total rows, NCL guard uses raw_text fallback,
         add Apple GT + identity check for all 5 samples
6067ef1 fix: Apple section-map and PUC share-count mis-match
d15ef15 chore: modular per-sample ground truth, fix ITE sign, update paths
e3a0c71 feat: full KO 10-K support
```

## What was fixed last session

1. **NCL tiebreak** (`field_extractor.py:433`): When two rows tie on score/section/page, prefer the row with "total" in the description. Fixes Apple NCL picking "Other non-current liabilities" (component, 49,848) over "Total non-current liabilities" (total, 145,129).

2. **NCL guard raw_text fallback** (`repair_engine.py:503-510`): `_apply_ncl_from_tl_cl` now checks `raw_text` via `parse_numeric()` when `value` is still None, so the guard preserves keyword-match results before value parsing.

3. **Apple GT** (`tests/gt/AAPL.json`): 48 field-year validations from Apple FY2023 10-K. All values verified against filed 10-K (Nov 2, 2023).

## Known limitations (what will break first in the wild)

- **Only 2 real 10-Ks** (KO, Apple), both US GAAP, both text-based PDFs. Every new document is a leap.
- **SECTION_MARKERS are hardcoded** — untested 10-K with novel headers → all pages "unknown" → no section bonus → higher false-match risk.
- **ASML corrupt** (345B download error), **M&S** has only 6 extractable text rows (no tables) — both non-starters.
- **Validation is partially circular**: repair engine forces identities to balance, then validate_fields checks the same identities. A compensating error could balance while both values are wrong.
- **Inverse search only runs in `--optimize` mode** (default on). Without it, missing fields stay missing.

## How to run

```powershell
# Full evaluation (200 fields across 5 samples)
python -m tests.evaluate

# Identity check (accounting equations)
python tests/identity_check.py

# Single file
python -m src.main data/real_10ks/aapl_10k.pdf --optimize
```

## Ground truth files (`tests/gt/`)

| File       | Type        | Status |
|------------|-------------|--------|
| SAMPLE1.json | Synthetic | Stable |
| SAMPLE2.json | Synthetic | Stable |
| SAMPLE3.json | Synthetic | Stable |
| KO.json      | Real 10-K  | Stable |
| AAPL.json    | Real 10-K  | New — commit 974e024 |

## Data files (`data/`)

| File | Status |
|------|--------|
| `samples/AA_SAMPLE1-3.pdf` | Synthetic, stable |
| `real_10ks/ko_10k.pdf` | ~190pp, text-based, stable |
| `real_10ks/aapl_10k.pdf` | ~50pp, text-based, stable |
| `real_10ks/asml_2023.pdf` | 345B — corrupted download |
| `real_10ks/mks_2023.pdf` | 161KB — only 6 text rows, no tables |

## Pipeline architecture key files

- `src/field_config.yaml` — Keywords, sections, signs, restricting per field
- `src/field_extractor.py` — Keyword matching, scoring, tiebreak, section bonus
- `src/table_builder.py` — Text → table rows, section markers, row merge
- `src/repair_engine.py` — Math repairs, pre-phase inference, solver loop
- `src/main.py` — Orchestration, value parsing, multi-year propagation

## Next steps (if continuing)

1. **Download a new real 10-K** (e.g. MSFT, JNJ, PEP) and either verify or find new failure modes
2. **Audit field_config.yaml** keywords for gaps against more 10-Ks
3. **Resolve ASML/M&S** — re-download valid PDFs for non-US-GAAP stress test
