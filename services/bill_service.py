"""Bills, bill-line associations and shares. Every mutating function enforces require_admin."""
from __future__ import annotations

from core.models import Bill, Share, new_id
from core.snapshot import load_snapshot
from services.common import (
    BusinessRuleError, ConfirmationRequired, Principal, ServiceContext, audit, require_admin,
)
from services.repository import DuplicateError, NotFoundError
from utils.calculations import confirmed_paid, equal_split, reconcile
from utils.validators import ValidationError, parse_date, parse_money, parse_month


def _validate_lines(snap, line_ids: list[str]) -> list[str]:
    ids = list(dict.fromkeys(line_ids))
    if not ids:
        raise ValidationError("Select at least one mobile line.")
    for lid in ids:
        line = snap.line(lid)
        if line is None or not line.active:
            raise BusinessRuleError("Bills can only be associated with existing active lines.")
    return ids


def _check_removed_lines(snap, bill_id: str, wanted: list[str]) -> None:
    for lid in set(snap.bill_line_ids(bill_id)) - set(wanted):
        if any(s.bill_id == bill_id and s.line_id == lid for s in snap.shares):
            raise BusinessRuleError("Remove the line's share before dissociating it from the bill.")


def create_bill(ctx: ServiceContext, actor: Principal, *, billing_month: str, total_bill,
                due_date, line_ids: list[str]) -> Bill:
    require_admin(ctx, actor)
    month = parse_month(billing_month)
    total = parse_money(total_bill, "Total bill")
    due = parse_date(due_date, "Due date")
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        ids = _validate_lines(snap, line_ids)
        for b in snap.bills:
            if b.billing_month == month and b.bill_status != "closed" and set(snap.bill_line_ids(b.bill_id)) == set(ids):
                raise DuplicateError("A bill for these lines already exists in that month.")
        now = ctx.stamp()
        bill = Bill(new_id("bill"), month, total, due, "draft", now, now, tuple(ids))
        ctx.repo.append("Bills", bill.to_row())
        audit(ctx, actor, "bill.create", "bill", bill.bill_id, f"month={month} total={total}")
    return bill


def update_bill(ctx: ServiceContext, actor: Principal, bill_id: str, *, total_bill=None, due_date=None,
                line_ids: list[str] | None = None, expected_updated_at: str | None = None,
                confirm_material_change: bool = False) -> None:
    require_admin(ctx, actor)
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        bill = snap.bill(bill_id)
        if bill is None:
            raise NotFoundError("Bill not found.")
        if bill.bill_status == "closed":
            raise BusinessRuleError("Closed bills cannot be edited.")
        total = parse_money(total_bill, "Total bill") if total_bill is not None else bill.total_bill
        due = parse_date(due_date, "Due date") if due_date is not None else bill.bill_due_date
        current_lines_list = snap.bill_line_ids(bill_id)
        current_lines = set(current_lines_list)
        wanted = _validate_lines(snap, line_ids) if line_ids is not None else None
        material = total != bill.total_bill or due != bill.bill_due_date or \
            (wanted is not None and set(wanted) != current_lines)
        if bill.bill_status == "published" and material and not confirm_material_change:
            raise ConfirmationRequired("Changing a published bill requires explicit confirmation.")
        if wanted is not None:
            _check_removed_lines(snap, bill_id, wanted)
        ctx.repo.update("Bills", bill_id, {"total_bill": str(total), "bill_due_date": due.isoformat(),
                                           "line_ids": ",".join(wanted if wanted is not None else current_lines_list),
                                           "updated_at": ctx.stamp()}, expected_updated_at=expected_updated_at)
        audit(ctx, actor, "bill.update", "bill", bill_id,
              f"total {bill.total_bill}->{total}; due {bill.bill_due_date}->{due}; status={bill.bill_status}")


def bill_reconciliation(snap, bill_id: str):
    bill = snap.bill(bill_id)
    if bill is None:
        raise NotFoundError("Bill not found.")
    return reconcile(bill.total_bill, [s.amount_due for s in snap.shares if s.bill_id == bill_id])


def publish_bill(ctx: ServiceContext, actor: Principal, bill_id: str, *, confirm: bool = False,
                 expected_updated_at: str | None = None) -> None:
    require_admin(ctx, actor)
    if not confirm:
        raise ConfirmationRequired("Publishing makes this bill visible to users; confirm to proceed.")
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        bill = snap.bill(bill_id)
        if bill is None:
            raise NotFoundError("Bill not found.")
        if bill.bill_status != "draft":
            raise BusinessRuleError("Only draft bills can be published.")
        shares = [s for s in snap.shares if s.bill_id == bill_id]
        if not shares:
            raise BusinessRuleError("Create shares before publishing.")
        rec = reconcile(bill.total_bill, [s.amount_due for s in shares])
        if not rec.ok:
            raise BusinessRuleError(f"Shares do not reconcile with the bill total (difference {rec.difference}).")
        ctx.repo.update("Bills", bill_id, {"bill_status": "published", "updated_at": ctx.stamp()},
                        expected_updated_at=expected_updated_at)
        audit(ctx, actor, "bill.publish", "bill", bill_id)


def close_bill(ctx: ServiceContext, actor: Principal, bill_id: str, *, confirm: bool = False) -> None:
    """Closing freezes the bill. Unpaid balances remain visible as historical dues."""
    require_admin(ctx, actor)
    if not confirm:
        raise ConfirmationRequired("Closing a bill freezes it; confirm to proceed.")
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        bill = snap.bill(bill_id)
        if bill is None:
            raise NotFoundError("Bill not found.")
        if bill.bill_status != "published":
            raise BusinessRuleError("Only published bills can be closed.")
        ctx.repo.update("Bills", bill_id, {"bill_status": "closed", "updated_at": ctx.stamp()})
        audit(ctx, actor, "bill.close", "bill", bill_id)


def _share_row(share_id, bill_id, line_id, user_id, amount, now):
    return {"share_id": share_id, "bill_id": bill_id, "line_id": line_id, "user_id": user_id,
            "amount_due": str(amount), "amount_paid": "0.00", "payment_status": "unpaid", "updated_at": now}


def create_share(ctx: ServiceContext, actor: Principal, *, bill_id: str, line_id: str, amount_due,
                 confirm_material_change: bool = False) -> Share:
    require_admin(ctx, actor)
    amount = parse_money(amount_due, "Amount due")
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        bill = snap.bill(bill_id)
        if bill is None:
            raise NotFoundError("Bill not found.")
        if bill.bill_status == "closed":
            raise BusinessRuleError("Closed bills cannot be changed.")
        if bill.bill_status == "published" and not confirm_material_change:
            raise ConfirmationRequired("Adding a share to a published bill requires confirmation.")
        if line_id not in snap.bill_line_ids(bill_id):
            raise BusinessRuleError("That line is not associated with this bill.")
        line = snap.line(line_id)
        if line is None or not line.active:
            raise BusinessRuleError("The line is inactive or missing.")
        if any(s.bill_id == bill_id and s.line_id == line_id for s in snap.shares):
            raise DuplicateError("A share for this bill and line already exists.")
        share = Share(new_id("shr"), bill_id, line_id, line.user_id, amount, ctx.stamp())
        ctx.repo.append("BillShares", _share_row(share.share_id, bill_id, line_id, line.user_id, amount, share.updated_at))
        audit(ctx, actor, "share.create", "share", share.share_id, f"amount={amount}")
    return share


def generate_equal_shares(ctx: ServiceContext, actor: Principal, bill_id: str) -> list[Share]:
    """Convenience: split a draft bill's total equally across its lines (only when no shares exist)."""
    require_admin(ctx, actor)
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        bill = snap.bill(bill_id)
        if bill is None:
            raise NotFoundError("Bill not found.")
        if bill.bill_status != "draft":
            raise BusinessRuleError("Equal split is only available for draft bills.")
        if any(s.bill_id == bill_id for s in snap.shares):
            raise BusinessRuleError("Shares already exist for this bill.")
        lids = snap.bill_line_ids(bill_id)
        if not lids:
            raise BusinessRuleError("The bill has no lines.")
        out = []
        for lid, amt in zip(lids, equal_split(bill.total_bill, len(lids))):
            line = snap.line(lid)
            share = Share(new_id("shr"), bill_id, lid, line.user_id, amt, ctx.stamp())
            ctx.repo.append("BillShares", _share_row(share.share_id, bill_id, lid, line.user_id, amt, share.updated_at))
            out.append(share)
        audit(ctx, actor, "share.equal_split", "bill", bill_id, f"shares={len(out)}")
    return out


def update_share(ctx: ServiceContext, actor: Principal, share_id: str, *, amount_due,
                 expected_updated_at: str | None = None, confirm_material_change: bool = False) -> None:
    require_admin(ctx, actor)
    amount = parse_money(amount_due, "Amount due")
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        share = snap.share(share_id)
        if share is None:
            raise NotFoundError("Share not found.")
        bill = snap.bill(share.bill_id)
        if bill.bill_status == "closed":
            raise BusinessRuleError("Closed bills cannot be changed.")
        if bill.bill_status == "published" and not confirm_material_change:
            raise ConfirmationRequired("Changing a published share requires confirmation.")
        paid = confirmed_paid(snap.payments, share_id)
        if amount < paid:
            raise BusinessRuleError(f"Amount due cannot be lower than confirmed payments ({paid}).")
        ctx.repo.update("BillShares", share_id, {"amount_due": str(amount), "updated_at": ctx.stamp()},
                        expected_updated_at=expected_updated_at)
        from services.payment_service import refresh_share_cache
        refresh_share_cache(ctx, share_id)
        audit(ctx, actor, "share.update", "share", share_id, f"amount {share.amount_due}->{amount}")
