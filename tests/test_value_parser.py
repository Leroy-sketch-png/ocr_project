import pytest
from src.value_parser import parse_numeric

def test_parse_numeric():
    assert parse_numeric("(87,557)") == -87557.0
    assert parse_numeric("14,095,953") == 14095953.0
    assert parse_numeric("-") == 0.0
    assert parse_numeric(None) is None
    assert parse_numeric("2023") == 2023.0
    assert parse_numeric("abc") is None
