"""Marking shares paid or unpaid. A share is simply paid or not; the change is recorded in the audit log."""
from __future__ import annotations

from core.snapshot import load_snapshot
from services.common import BusinessRuleError, Principal, ServiceContext, audit, require_admin
from services.repository import NotFoundError


def set_share_paid(ctx: ServiceContext, actor: Principal, share_id: str, paid: bool) -> None:
    """Admin-only. Sets the share's paid flag (and the paid date); every change is audited."""
    require_admin(ctx, actor)
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        share = snap.share(share_id)
        if share is None:
            raise NotFoundError("Share not found.")
        if snap.bill(share.bill_id).bill_status == "draft":
            raise BusinessRuleError("Publish the bill before marking shares paid.")
        if share.paid == paid:
            return
        today = snap.settings.today(ctx.now())
        ctx.repo.update("BillShares", share_id, {"paid": "TRUE" if paid else "FALSE",
                                                 "paid_date": today.isoformat() if paid else "",
                                                 "updated_at": ctx.stamp()})
        audit(ctx, actor, "share.paid" if paid else "share.unpaid", "share", share_id, f"amount={share.amount_due}")
