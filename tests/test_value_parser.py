from src.value_parser import parse_numeric


def test_parse_numeric_standard() -> None:
    assert parse_numeric("1,234,567.89") == 1234567.89
    assert parse_numeric("1234") == 1234.0


def test_parse_numeric_negative() -> None:
    assert parse_numeric("(1,234.5)") == -1234.5
    assert parse_numeric("-1234") == -1234.0


def test_parse_numeric_dash() -> None:
    assert parse_numeric("-") == 0.0


def test_parse_numeric_invalid() -> None:
    assert parse_numeric("abc") is None
    assert parse_numeric("") is None
    assert parse_numeric(None) is None
