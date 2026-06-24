from src.table_builder import is_numeric_token


def test_is_numeric_token() -> None:
    assert is_numeric_token("1,234") is True
    assert is_numeric_token("(123.4)") is True
    assert is_numeric_token("-") is True
    assert is_numeric_token("Revenue") is False
