from src.filters import _validate_salary


def test_valid_salary():
    assert _validate_salary("5000", "8000", "EUR", "yearly") == "EUR 5000-8000"


def test_swapped_salary():
    assert _validate_salary("8000", "5000", "EUR", "yearly") == "EUR 5000-8000"


def test_garbage_salary():
    assert _validate_salary("4", "3", "EUR", "") == ""
    assert _validate_salary("abc", "def", "EUR", "") == ""
    assert _validate_salary("", "", "EUR", "") == ""


def test_single_value_salary():
    assert _validate_salary("5000", "", "EUR", "monthly") == "EUR 5000+"
    assert _validate_salary("", "8000", "EUR", "yearly") == "EUR 0-8000"
