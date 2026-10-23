"""Pure business rules. No I/O, no Streamlit. All money is Decimal.

Rules
- Only confirmed payments count (reversals are confirmed negative entries).
- outstanding = max(amount_due - confirmed_payments, 0)
- Status precedence: Paid (nothing owed) > Overdue (owed and past due) > Partial (some paid) > Due.
  A partially paid share that is past due is therefore "Overdue"; the paid amount is still shown.
- Aggregations sum *shares*, never bill totals, so a bill shared by N lines is counted once.
- Draft bills are excluded from every user-facing figure.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from core.models import Bill, MobileLine, Payment, Share, Snapshot, User

ZERO = Decimal("0.00")
TWO = Decimal("0.01")


class Status(str, Enum):
    PAID = "Paid"
    PARTIAL = "Partial"
    DUE = "Due"
    OVERDUE = "Overdue"


def q(x: Decimal) -> Decimal:
    return x.quantize(TWO)


def confirmed_paid(payments: list[Payment], share_id: str) -> Decimal:
    return q(sum((p.amount for p in payments
                  if p.share_id == share_id and p.payment_status == "confirmed"), ZERO))


def outstanding(amount_due: Decimal, paid: Decimal) -> Decimal:
    return q(max(amount_due - paid, ZERO))


def derive_status(amount_due: Decimal, paid: Decimal, due_date: date, today: date) -> Status:
    if outstanding(amount_due, paid) == 0:
        return Status.PAID
    if today > due_date:
        return Status.OVERDUE
    if paid > 0:
        return Status.PARTIAL
    return Status.DUE


def aggregate_status(views: list["ShareView"], today: date) -> Status:
    """Status of a group of shares, from the group's totals (not by averaging statuses)."""
    if not views:
        return Status.PAID
    if all(v.outstanding == 0 for v in views):
        return Status.PAID
    if any(v.outstanding > 0 and today > v.due_date for v in views):
        return Status.OVERDUE
    if sum((v.paid for v in views), ZERO) > 0:
        return Status.PARTIAL
    return Status.DUE


@dataclass(frozen=True)
class ShareView:
    share: Share
    bill: Bill
    line: MobileLine | None
    user: User | None
    paid: Decimal
    outstanding: Decimal
    status: Status

    @property
    def due_date(self) -> date:
        return self.bill.bill_due_date

    @property
    def month(self) -> str:
        return self.bill.billing_month

    @property
    def amount_due(self) -> Decimal:
        return self.share.amount_due


def build_share_views(snap: Snapshot, today: date, *, include_drafts: bool = False) -> list[ShareView]:
    paid_by_share: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for p in snap.payments:
        if p.payment_status == "confirmed":
            paid_by_share[p.share_id] += p.amount
    bills = {b.bill_id: b for b in snap.bills}
    lines = {x.line_id: x for x in snap.lines}
    users = {u.user_id: u for u in snap.users}
    views = []
    for s in snap.shares:
        bill = bills.get(s.bill_id)
        if bill is None or (bill.bill_status == "draft" and not include_drafts):
            continue
        paid = q(paid_by_share[s.share_id])
        views.append(ShareView(
            share=s, bill=bill, line=lines.get(s.line_id), user=users.get(s.user_id), paid=paid,
            outstanding=outstanding(s.amount_due, paid),
            status=derive_status(s.amount_due, paid, bill.bill_due_date, today),
        ))
    views.sort(key=lambda v: (v.month, v.line.line_label if v.line else ""), reverse=False)
    return views


@dataclass(frozen=True)
class PeriodSummary:
    period: str
    charges: Decimal
    collected: Decimal
    outstanding: Decimal
    paid_count: int
    outstanding_count: int
    overdue_amount: Decimal
    overdue_count: int
    previous_outstanding: Decimal
    previous_count: int


def current_views(views: list[ShareView], period: str) -> list[ShareView]:
    return [v for v in views if v.month == period]


def previous_views(views: list[ShareView], period: str, *, unpaid_only: bool = True) -> list[ShareView]:
    return [v for v in views if v.month < period and (v.outstanding > 0 or not unpaid_only)]


def summarize(views: list[ShareView], period: str, today: date) -> PeriodSummary:
    cur = current_views(views, period)
    prev = previous_views(views, period)
    overdue_cur = [v for v in cur if v.status is Status.OVERDUE]
    return PeriodSummary(
        period=period,
        charges=q(sum((v.amount_due for v in cur), ZERO)),
        collected=q(sum((min(v.paid, v.amount_due) for v in cur), ZERO)),
        outstanding=q(sum((v.outstanding for v in cur), ZERO)),
        paid_count=sum(1 for v in cur if v.status is Status.PAID),
        outstanding_count=sum(1 for v in cur if v.outstanding > 0),
        overdue_amount=q(sum((v.outstanding for v in overdue_cur), ZERO)),
        overdue_count=len(overdue_cur),
        previous_outstanding=q(sum((v.outstanding for v in prev), ZERO)),
        previous_count=len(prev),
    )


@dataclass(frozen=True)
class LineSummary:
    line: MobileLine
    current_due: Decimal
    current_paid: Decimal
    current_outstanding: Decimal
    status: Status
    previous: list[ShareView]
    previous_outstanding: Decimal


def line_summary(line: MobileLine, views: list[ShareView], period: str, today: date) -> LineSummary:
    mine = [v for v in views if v.share.line_id == line.line_id]
    cur = current_views(mine, period)
    prev = previous_views(mine, period)
    return LineSummary(
        line=line,
        current_due=q(sum((v.amount_due for v in cur), ZERO)),
        current_paid=q(sum((v.paid for v in cur), ZERO)),
        current_outstanding=q(sum((v.outstanding for v in cur), ZERO)),
        status=aggregate_status(cur, today),
        previous=prev,
        previous_outstanding=q(sum((v.outstanding for v in prev), ZERO)),
    )


@dataclass(frozen=True)
class Reconciliation:
    total: Decimal
    allocated: Decimal
    difference: Decimal  # total - allocated

    @property
    def ok(self) -> bool:
        return self.difference == 0


def reconcile(total: Decimal, share_amounts: list[Decimal]) -> Reconciliation:
    allocated = q(sum(share_amounts, ZERO))
    return Reconciliation(total=q(total), allocated=allocated, difference=q(total - allocated))


def equal_split(total: Decimal, n: int) -> list[Decimal]:
    """Split into n two-decimal parts that sum exactly to total (extra cents go to the first parts)."""
    if n <= 0:
        raise ValueError("n must be positive")
    cents = int((q(total) * 100).to_integral_value())
    base, extra = divmod(cents, n)
    return [Decimal(base + (1 if i < extra else 0)) / 100 for i in range(n)]


def monthly_bill_totals(bills: list[Bill]) -> dict[str, Decimal]:
    """Bill totals per month, one contribution per bill_id (shared bills are never duplicated)."""
    seen: dict[str, Bill] = {b.bill_id: b for b in bills if b.bill_status != "draft"}
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for b in seen.values():
        out[b.billing_month] += b.total_bill
    return {m: q(v) for m, v in sorted(out.items())}


def monthly_share_totals(views: list[ShareView]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for v in views:
        out[v.month] += v.amount_due
    return {m: q(v) for m, v in sorted(out.items())}


def total_paid(views: list[ShareView], payments: list[Payment]) -> tuple[Decimal, date | None]:
    """Net confirmed payments across the given shares (all months) and the earliest payment date."""
    ids = {v.share.share_id for v in views}
    mine = [p for p in payments if p.share_id in ids and p.payment_status == "confirmed"]
    since = min((p.payment_date for p in mine if p.amount > 0), default=None)
    return q(sum((p.amount for p in mine), ZERO)), since
