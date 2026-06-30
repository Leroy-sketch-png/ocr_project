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
