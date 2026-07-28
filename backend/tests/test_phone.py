import pytest

from app.phone import InvalidPhoneNumber, normalize_phone


def test_normalizes_canadian_number() -> None:
    assert normalize_phone("416 555 0123") == "+14165550123"


def test_rejects_invalid_number() -> None:
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone("123")
