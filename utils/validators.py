"""Input validation. Every function either returns a normalised value or raises ValidationError."""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

TWO_PLACES = Decimal("0.01")
MAX_AMOUNT = Decimal("1000000.00")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

ROLES = ("admin", "user")
BILL_STATUSES = ("draft", "published", "closed")
PAYMENT_STATUSES = ("pending", "confirmed", "rejected")
GUEST_POLICIES = ("none", "aggregate", "status_only")


class ValidationError(ValueError):
    """Raised for malformed or out-of-range input."""


def clean_text(value, field: str, *, max_len: int = 120, required: bool = True) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise ValidationError(f"{field} is required.")
    if len(text) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.")
    if any(ord(c) < 32 for c in text):
        raise ValidationError(f"{field} contains control characters.")
    return text


def normalize_email(value) -> str:
    email = clean_text(value, "Email", max_len=254).lower()
    if not _EMAIL_RE.match(email):
        raise ValidationError("Enter a valid email address.")
    return email


def parse_money(
    value,
    field: str = "Amount",
    *,
    allow_zero: bool = False,
    allow_negative: bool = False,
    maximum: Decimal = MAX_AMOUNT,
) -> Decimal:
    """Parse to a two-decimal Decimal. More than two decimals is rejected, never rounded."""
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field} is required.")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"{field} must be a number.")
        value = repr(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text:
        raise ValidationError(f"{field} is required.")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValidationError(f"{field} must be a number.") from None
    if not amount.is_finite():
        raise ValidationError(f"{field} must be a number.")
    if amount != amount.quantize(TWO_PLACES):
        raise ValidationError(f"{field} cannot have more than two decimal places.")
    amount = amount.quantize(TWO_PLACES)
    if amount < 0 and not allow_negative:
        raise ValidationError(f"{field} cannot be negative.")
    if amount == 0 and not allow_zero:
        raise ValidationError(f"{field} must be greater than zero.")
    if abs(amount) > maximum:
        raise ValidationError(f"{field} exceeds the maximum of {maximum}.")
    return amount


def parse_month(value, field: str = "Billing month") -> str:
    text = str(value or "").strip()
    if not _MONTH_RE.match(text):
        raise ValidationError(f"{field} must be in YYYY-MM format.")
    return text


def parse_date(value, field: str = "Date") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        raise ValidationError(f"{field} must be a valid date (YYYY-MM-DD).") from None


def parse_bool(value, field: str = "Flag") -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    raise ValidationError(f"{field} must be TRUE or FALSE.")


def normalize_phone(value) -> str:
    """Return digits only (7-15). Stored as text; displayed masked."""
    raw = clean_text(value, "Mobile number", max_len=32)
    if re.search(r"[^\d\s().+\-]", raw):
        raise ValidationError("Mobile number may contain only digits and + - ( ) . characters.")
    digits = re.sub(r"\D", "", raw)
    if not 7 <= len(digits) <= 15:
        raise ValidationError("Mobile number must have between 7 and 15 digits.")
    return digits


def choice(value, options: tuple[str, ...], field: str) -> str:
    text = str(value or "").strip().lower()
    if text not in options:
        raise ValidationError(f"{field} must be one of: {', '.join(options)}.")
    return text
