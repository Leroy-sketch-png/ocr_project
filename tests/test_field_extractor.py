import pytest
from src.field_extractor import compute_match_score, normalize_auditor_opinion

def test_compute_match_score():
    assert compute_match_score("trade receivables", "trade and other receivables") >= 82.0
    assert compute_match_score("revenue", "total revenue") >= 82.0
    assert compute_match_score("revenue", "cost of sales") < 82.0

def test_normalize_auditor_opinion():
    assert normalize_auditor_opinion("except for the matter described") == "Qualified"
    assert normalize_auditor_opinion("true and fair view") == "Unqualified"
    assert normalize_auditor_opinion("disclaimer of opinion") == "Disclaimer"

@pytest.mark.skip(reason="Confidence fallback reverted due to F1 regression on S3")
def test_confidence_based_fallback():
    # If implemented, the extractor should pick the cell with higher token confidence.
    # Currently reverted back to `>100` fallback.
    from src.models import TableRow, Token
    from src.field_extractor import extract_fields

    # Cell 0: '10' with 96% conf
    t1 = Token("10", 1, (0, 0, 10, 10), 96.0)
    # Cell 1: '72338' with 95% conf
    t2 = Token("72338", 1, (20, 0, 40, 10), 95.0)
    # Cell 2: 'abc' with 99% conf (unparseable, should be skipped)
    t3 = Token("abc", 1, (50, 0, 70, 10), 99.0)

    row = TableRow(
        page=1,
        description="Paid Up Capital",
        cells=["10", "72338", "abc"],
        cell_tokens=[[t1], [t2], [t3]]
    )
    
    config = {"Equity": {"Paid Up Capital": ["Paid Up Capital"]}}
    results = extract_fields([row], [], config, year_x_map={}, dpi_scale=1.0)
    
    # Under confidence-based logic, it would pick '10' (96.0 conf > 95.0 conf).
    # Unparseable 'abc' is skipped despite 99.0 conf.
    assert results["Paid Up Capital"] == 10.0

