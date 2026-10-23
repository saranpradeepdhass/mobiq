"""Payment reminders (manual, admin-triggered) and extension points for scheduled reminders / bill import.

send_payment_reminder() emails one user about one share, only when an admin explicitly triggers it.
Nothing here sends or publishes anything automatically.

Reminders: plan_reminders() is pure and only *proposes* who would be reminded. dispatch() refuses to
run unless the admin has enabled reminders in Settings AND a real channel was explicitly configured.
Bill import (e.g. AT&T PDF extraction): implement BillExtractor; results are drafts that an admin must
review and publish through bill_service (publish always requires explicit confirmation).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from datetime import datetime, timedelta, timezone

from core.snapshot import load_snapshot
from services.common import (
    AuthorizationError, BusinessRuleError, Principal, ServiceContext, audit, require_admin,
)
from services.mailer import Mailer
from services.repository import NotFoundError
from utils.calculations import ShareView, Status, build_share_views
from utils.formatting import date_label, format_money, mask_phone, month_label


@dataclass(frozen=True)
class ReminderRule:
    days_before_due: tuple[int, ...] = (3,)
    remind_when_overdue_every_days: int = 7


@dataclass(frozen=True)
class PlannedReminder:
    user_id: str
    share_id: str
    reason: str
    outstanding: Decimal


class ReminderChannel(Protocol):
    name: str

    def send(self, reminder: PlannedReminder, message: str) -> None: ...


def plan_reminders(views: list[ShareView], today: date, rule: ReminderRule = ReminderRule()) -> list[PlannedReminder]:
    out = []
    for v in views:
        if v.outstanding <= 0:
            continue
        days_left = (v.due_date - today).days
        if v.status is Status.OVERDUE:
            overdue_days = -days_left
            if rule.remind_when_overdue_every_days and overdue_days % rule.remind_when_overdue_every_days == 0:
                out.append(PlannedReminder(v.share.user_id, v.share.share_id, f"overdue {overdue_days}d", v.outstanding))
        elif days_left in rule.days_before_due:
            out.append(PlannedReminder(v.share.user_id, v.share.share_id, f"due in {days_left}d", v.outstanding))
    return out


def dispatch(ctx: ServiceContext, actor: Principal, plan: list[PlannedReminder],
             channel: ReminderChannel | None, reminders_enabled: bool) -> int:
    require_admin(ctx, actor)
    if not reminders_enabled or channel is None:
        raise AuthorizationError("Reminders are not enabled/configured; nothing was sent.")
    for r in plan:
        channel.send(r, f"Payment reminder: balance {r.outstanding} ({r.reason}).")
    return len(plan)


@dataclass(frozen=True)
class ExtractedBill:
    billing_month: str
    total_bill: Decimal
    due_date: date
    line_amounts: dict[str, Decimal]  # mobile number (digits) -> amount; matched to lines during review


class BillExtractor(Protocol):
    def extract(self, pdf_bytes: bytes) -> ExtractedBill: ...


REMINDER_COOLDOWN = timedelta(minutes=15)


def _recently_notified(ctx: ServiceContext, share_id: str) -> bool:
    cutoff = ctx.now() - REMINDER_COOLDOWN
    for r in ctx.repo.list("AuditLog", fresh=True):
        if r.get("action") == "reminder.send" and r.get("entity_id") == share_id:
            try:
                when = datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if when > cutoff:
                return True
    return False


def compose_reminder(app_name: str, view: ShareView, currency: str, instructions: str,
                     other_unpaid) -> tuple[str, str]:
    """(subject, body). The subject carries only amounts and dates, never free text from the sheet."""
    subject = f"Payment reminder: {format_money(view.outstanding, currency)} due {date_label(view.due_date)}"
    line = view.line.line_label if view.line else "your line"
    number = f" ({mask_phone(view.line.mobile_number)})" if view.line else ""
    name = view.user.name if view.user else "there"
    rows = [
        ("Line", f"{line}{number}"),
        ("Billing month", month_label(view.month)),
        ("Amount due", format_money(view.amount_due, currency)),
        ("Balance to pay", format_money(view.outstanding, currency)),
        ("Due date", date_label(view.due_date)),
        ("Status", view.status.value),
    ]
    width = max(len(k) for k, _ in rows)
    lines = [f"Hi {name},", "", f"This is a reminder from {app_name} about an unpaid mobile bill share:", ""]
    lines += [f"  {k.ljust(width)} : {v}" for k, v in rows]
    if other_unpaid:
        lines += ["", "Other unpaid bills on your lines:"]
        lines += [f"  {month_label(v.month)} - {v.line.line_label if v.line else ''}: {format_money(v.outstanding, currency)}"
                  for v in other_unpaid]
    if instructions:
        lines += ["", "How to pay:", f"  {instructions}"]
    lines += ["", "If you have already paid, please ignore this message."]
    return subject, "\n".join(lines)


def send_payment_reminder(ctx: ServiceContext, actor: Principal, share_id: str, mailer: Mailer | None) -> str:
    """Email the share's owner the payment details. Returns the recipient email (for the admin's confirmation)."""
    require_admin(ctx, actor)
    if mailer is None:
        raise BusinessRuleError("Email is not configured. Add [smtp] to the app secrets to send reminders.")
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        share = snap.share(share_id)
        if share is None:
            raise NotFoundError("Share not found.")
        views = build_share_views(snap, snap.settings.today(ctx.now()))
        view = next((v for v in views if v.share.share_id == share_id), None)
        if view is None:
            raise BusinessRuleError("Publish the bill before sending reminders.")
        if view.outstanding <= 0:
            raise BusinessRuleError("This share is already paid; no reminder needed.")
        if view.user is None or not view.user.active:
            raise BusinessRuleError("The owner of this line is missing or inactive.")
        if _recently_notified(ctx, share_id):
            raise BusinessRuleError("A reminder for this share was sent in the last 15 minutes.")
        others = [v for v in views if v.share.user_id == share.user_id and v.share.share_id != share_id
                  and v.outstanding > 0 and v.month <= snap.settings.period(ctx.now())]
        subject, body = compose_reminder(snap.settings.app_name, view, snap.settings.currency,
                                         snap.settings.payment_instructions, others)
        mailer.send_message(view.user.email, subject, body)  # raises DeliveryError on failure (nothing audited)
        audit(ctx, actor, "reminder.send", "share", share_id, f"outstanding={view.outstanding}")
        return view.user.email
