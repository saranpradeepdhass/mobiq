"""Payments. The admin marks a share paid or unpaid with set_share_paid(); payment rows are never edited
or deleted.

- Marking paid appends one confirmed payment for the remaining balance.
- Marking unpaid appends a reversal (confirmed, negative, reverses_payment_id -> original) for every
  confirmed payment still in effect, so both rows stay in the audit trail.
"""
from __future__ import annotations

from core.models import Payment, new_id
from core.snapshot import load_snapshot
from services.common import BusinessRuleError, Principal, ServiceContext, audit, require_admin
from services.repository import NotFoundError
from utils.calculations import derive_status, confirmed_paid


def refresh_share_cache(ctx: ServiceContext, share_id: str) -> None:
    """Write derived amount_paid / payment_status back to the BillShares sheet for readability.
    Calculations never read these columns; they are always derived from Payments."""
    snap = load_snapshot(ctx.repo, fresh=True)
    share = snap.share(share_id)
    if share is None:
        return
    bill = snap.bill(share.bill_id)
    paid = confirmed_paid(snap.payments, share_id)
    today = snap.settings.today(ctx.now())
    status = derive_status(share.amount_due, paid, bill.bill_due_date, today)
    ctx.repo.update("BillShares", share_id, {"amount_paid": str(paid),
                                             "payment_status": status.value.lower(),
                                             "updated_at": ctx.stamp()})


def _append_payment(ctx, snap, actor, share, amount, method, notes, reverses=""):
    payment = Payment(new_id("pay"), share.share_id, share.user_id, amount, snap.settings.today(ctx.now()),
                      "confirmed", method, notes[:300], ctx.stamp(), reverses_payment_id=reverses)
    ctx.repo.append("Payments", payment.to_row())
    audit(ctx, actor, "payment.reverse" if reverses else "payment.record", "payment", payment.payment_id,
          f"share={share.share_id} amount={amount}" + (f" reverses={reverses}" if reverses else ""))
    return payment


def set_share_paid(ctx: ServiceContext, actor: Principal, share_id: str, paid: bool) -> None:
    """Admin-only toggle. paid=True settles the remaining balance; paid=False reverses all confirmed payments."""
    require_admin(ctx, actor)
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        share = snap.share(share_id)
        if share is None:
            raise NotFoundError("Share not found.")
        if snap.bill(share.bill_id).bill_status == "draft":
            raise BusinessRuleError("Publish the bill before recording payments.")
        owed = share.amount_due - confirmed_paid(snap.payments, share_id)
        if paid and owed > 0:
            _append_payment(ctx, snap, actor, share, owed, "Marked paid by admin", "")
        elif not paid:
            reversed_ids = {p.reverses_payment_id for p in snap.payments if p.reverses_payment_id}
            for p in snap.payments:
                if (p.share_id == share_id and p.payment_status == "confirmed" and p.amount > 0
                        and not p.reverses_payment_id and p.payment_id not in reversed_ids):
                    _append_payment(ctx, snap, actor, share, -p.amount, p.payment_method,
                                    f"Reversal of {p.payment_id}: marked unpaid by admin", reverses=p.payment_id)
        refresh_share_cache(ctx, share_id)
