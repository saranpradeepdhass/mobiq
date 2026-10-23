"""Display helpers: money, masked phone numbers, month labels, timestamps."""
from __future__ import annotations

import html
from datetime import date, datetime, timezone
from decimal import Decimal

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "$", "AUD": "$", "INR": "₹"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    # Millisecond precision: updated_at doubles as the optimistic-concurrency token.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def format_money(amount: Decimal, currency: str = "USD") -> str:
    symbol = _SYMBOLS.get(currency.upper(), currency.upper() + " ")
    sign = "-" if amount < 0 else ""
    return f"{sign}{symbol}{abs(amount):,.2f}"


def mask_phone(digits: str) -> str:
    """Show only the last four digits."""
    tail = (digits or "")[-4:]
    return f"•••• {tail}" if tail else "••••"


def month_label(month: str) -> str:
    try:
        return date(int(month[:4]), int(month[5:7]), 1).strftime("%b %Y")
    except (ValueError, IndexError):
        return month or "—"


def date_label(d: date | None) -> str:
    return d.strftime("%b %d, %Y") if d else "—"


def esc(value) -> str:
    """HTML-escape any value destined for unsafe_allow_html rendering."""
    return html.escape("" if value is None else str(value), quote=True)
