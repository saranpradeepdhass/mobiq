from decimal import Decimal as D

import pytest

from utils.formatting import format_money, mask_phone
from utils.validators import (
    ValidationError, normalize_email, normalize_phone, parse_bool, parse_date, parse_money, parse_month,
)


@pytest.mark.parametrize("bad", ["", "abc", "-5", "0", "1.234", "NaN", "Infinity", "2000000", None, True])
def test_invalid_money(bad):
    with pytest.raises(ValidationError):
        parse_money(bad)


def test_money_parsing():
    assert parse_money("1,234.50") == D("1234.50")
    assert parse_money(12) == D("12.00")
    assert parse_money("0", allow_zero=True) == D("0.00")
    assert parse_money(0.1) == D("0.10")


@pytest.mark.parametrize("bad", ["2026-13", "26-01", "2026/01", "", "2026-1"])
def test_invalid_month(bad):
    with pytest.raises(ValidationError):
        parse_month(bad)


def test_email_phone_date_bool():
    assert normalize_email("  Alex@Example.COM ") == "alex@example.com"
    with pytest.raises(ValidationError):
        normalize_email("not-an-email")
    assert normalize_phone("(555) 010-0002") == "5550100002"
    with pytest.raises(ValidationError):
        normalize_phone("12ab")
    with pytest.raises(ValidationError):
        parse_date("2026-02-30")
    assert parse_bool("TRUE") is True and parse_bool("no") is False
    with pytest.raises(ValidationError):
        parse_bool("maybe")


def test_formatting():
    assert format_money(D("1234.5")) == "$1,234.50"
    assert format_money(D("-3")) == "-$3.00"
    assert mask_phone("5550100002") == "•••• 0002"
    assert "5550" not in mask_phone("5550100002")
