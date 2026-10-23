from datetime import timedelta
from decimal import Decimal as D

import pytest

from core.snapshot import load_snapshot
from services import bill_service, reminder_service
from services.common import GUEST, AuthorizationError, BusinessRuleError
from services.mailer import DeliveryError, OutboxMailer
from services.repository import NotFoundError
from services import payment_service


def published(ctx, admin):
    b = bill_service.create_bill(ctx, admin, billing_month="2026-10", total_bill="100.00", due_date="2026-10-20",
                                 line_ids=["l_alex1", "l_sam"])
    for lid, amt in (("l_alex1", "60.00"), ("l_sam", "40.00")):
        bill_service.create_share(ctx, admin, bill_id=b.bill_id, line_id=lid, amount_due=amt)
    bill_service.publish_bill(ctx, admin, b.bill_id, confirm=True)
    snap = load_snapshot(ctx.repo)
    return {s.line_id: s.share_id for s in snap.shares}


def test_reminder_email_has_due_details_and_masks_number(world, ctx, admin):
    shares = published(ctx, admin)
    mailer = OutboxMailer()
    to = reminder_service.send_payment_reminder(ctx, admin, shares["l_sam"], mailer)
    assert to == "sam@example.com" and len(mailer.sent) == 1
    _, subject, body = mailer.sent[0]
    assert "$40.00" in subject and "Oct 20, 2026" in subject
    for expected in ("Sam phone", "•••• 0004", "$40.00", "Oct 2026", "Overdue"):
        assert expected in body
    assert "5550100004" not in body
    assert "reminder.send" in [r["action"] for r in ctx.repo.list("AuditLog")]


def test_reminder_includes_other_unpaid_bills_and_instructions(world, ctx, admin):
    shares = published(ctx, admin)
    ctx.repo.append("Settings", {"key": "payment_instructions", "value": "Zelle billing@example.com", "updated_at": "t"})
    mailer = OutboxMailer()
    reminder_service.send_payment_reminder(ctx, admin, shares["l_alex1"], mailer)
    assert "Zelle billing@example.com" in mailer.sent[0][2]


def test_only_admin_can_notify(world, ctx, alex):
    for actor in (alex, GUEST):
        with pytest.raises(AuthorizationError):
            reminder_service.send_payment_reminder(ctx, actor, "x", OutboxMailer())


def test_no_mailer_paid_share_draft_and_unknown(world, ctx, admin):
    shares = published(ctx, admin)
    with pytest.raises(BusinessRuleError, match="not configured"):
        reminder_service.send_payment_reminder(ctx, admin, shares["l_sam"], None)
    payment_service.set_share_paid(ctx, admin, shares["l_sam"], True)
    mailer = OutboxMailer()
    with pytest.raises(BusinessRuleError, match="already paid"):
        reminder_service.send_payment_reminder(ctx, admin, shares["l_sam"], mailer)
    with pytest.raises(NotFoundError):
        reminder_service.send_payment_reminder(ctx, admin, "nope", mailer)
    b = bill_service.create_bill(ctx, admin, billing_month="2026-11", total_bill="10", due_date="2026-11-20", line_ids=["l_sam"])
    bill_service.create_share(ctx, admin, bill_id=b.bill_id, line_id="l_sam", amount_due="10")
    draft_share = next(s.share_id for s in load_snapshot(ctx.repo).shares if s.bill_id == b.bill_id)
    with pytest.raises(BusinessRuleError, match="Publish"):
        reminder_service.send_payment_reminder(ctx, admin, draft_share, mailer)
    assert mailer.sent == []


def test_cooldown_blocks_repeat_reminders(world, ctx, admin):
    shares = published(ctx, admin)
    mailer = OutboxMailer()
    reminder_service.send_payment_reminder(ctx, admin, shares["l_sam"], mailer)
    with pytest.raises(BusinessRuleError, match="15 minutes"):
        reminder_service.send_payment_reminder(ctx, admin, shares["l_sam"], mailer)
    assert len(mailer.sent) == 1


def test_inactive_owner_is_not_emailed(world, ctx, admin):
    shares = published(ctx, admin)
    ctx.repo.update("Users", "u_sam", {"active": "FALSE"})
    mailer = OutboxMailer()
    with pytest.raises(BusinessRuleError):
        reminder_service.send_payment_reminder(ctx, admin, shares["l_sam"], mailer)
    assert mailer.sent == []


def test_delivery_failure_surfaces_and_is_not_audited(world, ctx, admin):
    shares = published(ctx, admin)

    class Broken:
        def send_message(self, to, subject, body):
            raise DeliveryError("smtp down")

    with pytest.raises(DeliveryError):
        reminder_service.send_payment_reminder(ctx, admin, shares["l_sam"], Broken())
    assert "reminder.send" not in [r["action"] for r in ctx.repo.list("AuditLog")]  # so the admin can retry at once
