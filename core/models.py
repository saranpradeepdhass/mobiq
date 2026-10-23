"""Domain models, worksheet schemas and row parsing.

Row parsing validates every field; malformed rows raise ValidationError so the loader can
skip them and surface a warning instead of crashing the app.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utils.validators import (
    BILL_STATUSES, GUEST_POLICIES, ROLES, ValidationError, choice,
    clean_text, normalize_email, normalize_phone, parse_bool, parse_date, parse_money, parse_month,
)

# worksheet -> (id column, headers)
TABLES: dict[str, tuple[str, list[str]]] = {
    "Users": ("user_id", ["user_id", "name", "email", "role", "active", "created_at"]),
    "MobileLines": ("line_id", ["line_id", "user_id", "line_label", "mobile_number",
                                "account_reference", "active", "created_at", "updated_at"]),
    "Bills": ("bill_id", ["bill_id", "billing_month", "total_bill", "bill_due_date",
                          "bill_status", "created_at", "updated_at", "line_ids"]),
    "BillShares": ("share_id", ["share_id", "bill_id", "line_id", "user_id", "amount_due",
                                "paid", "paid_date", "updated_at"]),
    "Settings": ("key", ["key", "value", "updated_at"]),
    "AuditLog": ("audit_id", ["audit_id", "timestamp", "actor_user_id", "action",
                              "entity_type", "entity_id", "details"]),
}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def fmt_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


@dataclass(frozen=True)
class User:
    user_id: str
    name: str
    email: str
    role: str
    active: bool
    created_at: str

    @classmethod
    def from_row(cls, r: dict) -> "User":
        return cls(
            user_id=clean_text(r.get("user_id"), "user_id", max_len=64),
            name=clean_text(r.get("name"), "name", max_len=80),
            email=normalize_email(r.get("email")),
            role=choice(r.get("role"), ROLES, "role"),
            active=parse_bool(r.get("active"), "active"),
            created_at=str(r.get("created_at") or ""),
        )

    def to_row(self) -> dict:
        return {"user_id": self.user_id, "name": self.name, "email": self.email, "role": self.role,
                "active": fmt_bool(self.active), "created_at": self.created_at}


@dataclass(frozen=True)
class MobileLine:
    line_id: str
    user_id: str
    line_label: str
    mobile_number: str  # digits only
    account_reference: str
    active: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, r: dict) -> "MobileLine":
        return cls(
            line_id=clean_text(r.get("line_id"), "line_id", max_len=64),
            user_id=clean_text(r.get("user_id"), "user_id", max_len=64),
            line_label=clean_text(r.get("line_label"), "line_label", max_len=40),
            mobile_number=normalize_phone(r.get("mobile_number")),
            account_reference=clean_text(r.get("account_reference"), "account_reference",
                                         max_len=60, required=False),
            active=parse_bool(r.get("active"), "active"),
            created_at=str(r.get("created_at") or ""),
            updated_at=str(r.get("updated_at") or ""),
        )

    def to_row(self) -> dict:
        return {"line_id": self.line_id, "user_id": self.user_id, "line_label": self.line_label,
                "mobile_number": self.mobile_number, "account_reference": self.account_reference,
                "active": fmt_bool(self.active), "created_at": self.created_at,
                "updated_at": self.updated_at}


@dataclass(frozen=True)
class Bill:
    bill_id: str
    billing_month: str
    total_bill: Decimal
    bill_due_date: date
    bill_status: str
    created_at: str
    updated_at: str
    line_ids: tuple[str, ...] = ()  # mobile lines this bill covers (comma-separated in the sheet)

    @classmethod
    def from_row(cls, r: dict) -> "Bill":
        return cls(
            bill_id=clean_text(r.get("bill_id"), "bill_id", max_len=64),
            billing_month=parse_month(r.get("billing_month")),
            total_bill=parse_money(r.get("total_bill"), "total_bill", allow_zero=True),
            bill_due_date=parse_date(r.get("bill_due_date"), "bill_due_date"),
            bill_status=choice(r.get("bill_status"), BILL_STATUSES, "bill_status"),
            created_at=str(r.get("created_at") or ""),
            updated_at=str(r.get("updated_at") or ""),
            line_ids=tuple(dict.fromkeys(x.strip() for x in str(r.get("line_ids") or "").split(",") if x.strip())),
        )

    def to_row(self) -> dict:
        return {"bill_id": self.bill_id, "line_ids": ",".join(self.line_ids), "billing_month": self.billing_month,
                "total_bill": str(self.total_bill), "bill_due_date": self.bill_due_date.isoformat(),
                "bill_status": self.bill_status, "created_at": self.created_at,
                "updated_at": self.updated_at}


@dataclass(frozen=True)
class Share:
    """One line's share of a bill. Payment is a simple flag: paid or not (no partial payments)."""
    share_id: str
    bill_id: str
    line_id: str
    user_id: str
    amount_due: Decimal
    updated_at: str
    paid: bool = False
    paid_date: date | None = None

    @classmethod
    def from_row(cls, r: dict) -> "Share":
        paid = parse_bool(r.get("paid") or "FALSE", "paid")
        raw_date = str(r.get("paid_date") or "").strip()
        return cls(
            share_id=clean_text(r.get("share_id"), "share_id", max_len=64),
            bill_id=clean_text(r.get("bill_id"), "bill_id", max_len=64),
            line_id=clean_text(r.get("line_id"), "line_id", max_len=64),
            user_id=clean_text(r.get("user_id"), "user_id", max_len=64),
            amount_due=parse_money(r.get("amount_due"), "amount_due", allow_zero=True),
            updated_at=str(r.get("updated_at") or ""),
            paid=paid,
            paid_date=parse_date(raw_date, "paid_date") if (paid and raw_date) else None,
        )


@dataclass(frozen=True)
class AppSettings:
    app_name: str = "Mobiq"
    currency: str = "USD"
    timezone: str = "America/New_York"
    current_billing_period: str = ""      # blank -> current month in `timezone`
    default_due_day: int = 20
    guest_visibility: str = "none"        # none | aggregate | status_only
    payment_instructions: str = ""
    reminders_enabled: bool = False

    @classmethod
    def from_rows(cls, rows: list[dict]) -> tuple["AppSettings", list[str]]:
        """Parse key/value rows; invalid values fall back to defaults with a warning."""
        kv = {str(r.get("key", "")).strip(): str(r.get("value", "")).strip() for r in rows}
        d = cls()
        warnings: list[str] = []

        def attempt(key, parse, default):
            if key not in kv or kv[key] == "":
                return default
            try:
                return parse(kv[key])
            except (ValidationError, ValueError, ZoneInfoNotFoundError) as exc:
                warnings.append(f"Setting '{key}' is invalid ({exc}); using default.")
                return default

        def tz(v):
            ZoneInfo(v)
            return v

        def day(v):
            n = int(v)
            if not 1 <= n <= 28:
                raise ValueError("must be 1-28")
            return n

        return cls(
            app_name=attempt("app_name", lambda v: clean_text(v, "app_name", max_len=40), d.app_name),
            currency=attempt("currency", lambda v: clean_text(v, "currency", max_len=3).upper(), d.currency),
            timezone=attempt("timezone", tz, d.timezone),
            current_billing_period=attempt("current_billing_period", parse_month, ""),
            default_due_day=attempt("default_due_day", day, d.default_due_day),
            guest_visibility=attempt("guest_visibility",
                                     lambda v: choice(v, GUEST_POLICIES, "guest_visibility"), "none"),
            payment_instructions=attempt("payment_instructions",
                                         lambda v: clean_text(v, "payment_instructions", max_len=500, required=False), ""),
            reminders_enabled=attempt("reminders_enabled", lambda v: parse_bool(v), False),
        ), warnings

    def today(self, now) -> date:
        return now.astimezone(ZoneInfo(self.timezone)).date()

    def period(self, now) -> str:
        return self.current_billing_period or self.today(now).strftime("%Y-%m")


@dataclass
class Snapshot:
    """A consistent, validated in-memory view of all worksheets."""
    users: list[User] = field(default_factory=list)
    lines: list[MobileLine] = field(default_factory=list)
    bills: list[Bill] = field(default_factory=list)
    shares: list[Share] = field(default_factory=list)
    settings: AppSettings = field(default_factory=AppSettings)
    warnings: list[str] = field(default_factory=list)

    def user(self, user_id: str) -> User | None:
        return next((u for u in self.users if u.user_id == user_id), None)

    def line(self, line_id: str) -> MobileLine | None:
        return next((x for x in self.lines if x.line_id == line_id), None)

    def bill(self, bill_id: str) -> Bill | None:
        return next((b for b in self.bills if b.bill_id == bill_id), None)

    def share(self, share_id: str) -> Share | None:
        return next((s for s in self.shares if s.share_id == share_id), None)

    def bill_line_ids(self, bill_id: str) -> list[str]:
        bill = self.bill(bill_id)
        return list(bill.line_ids) if bill else []
