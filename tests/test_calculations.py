from datetime import date
from decimal import Decimal as D

from core.snapshot import load_snapshot
from services import bill_service, payment_service
from services.access_service import visible_views
from utils.calculations import (
    Status, build_share_views, derive_status, equal_split, line_summary, monthly_bill_totals,
    outstanding, reconcile, summarize,
)

TODAY = date(2026, 10, 22)


def test_outstanding_never_negative():
    assert outstanding(D("10.00"), D("12.00")) == D("0.00")
    assert outstanding(D("10.00"), D("2.50")) == D("7.50")


def test_status_paid_due_partial_overdue():
    future, past = date(2026, 10, 30), date(2026, 10, 1)
    assert derive_status(D("40"), D("40"), future, TODAY) is Status.PAID
    assert derive_status(D("40"), D("40"), past, TODAY) is Status.PAID       # paid late is still paid
    assert derive_status(D("40"), D("0"), future, TODAY) is Status.DUE
    assert derive_status(D("40"), D("0"), past, TODAY) is Status.OVERDUE
    assert derive_status(D("40"), D("0"), TODAY, TODAY) is Status.DUE        # due today is not overdue


def _bill(ctx, admin, month, total, due, lines, shares, publish=True):
    b = bill_service.create_bill(ctx, admin, billing_month=month, total_bill=total, due_date=due, line_ids=lines)
    for lid, amt in zip(lines, shares):
        bill_service.create_share(ctx, admin, bill_id=b.bill_id, line_id=lid, amount_due=amt)
    if publish:
        bill_service.publish_bill(ctx, admin, b.bill_id, confirm=True)
    return b


def _pay(ctx, admin, bill, line, amount):
    snap = load_snapshot(ctx.repo)
    sid = next(s.share_id for s in snap.shares if s.bill_id == bill.bill_id and s.line_id == line)
    payment_service.set_share_paid(ctx, admin, sid, True)  # a share is simply paid or not


def _views(ctx):
    snap = load_snapshot(ctx.repo)
    return snap, build_share_views(snap, TODAY)


def test_current_payment_does_not_hide_previous_arrears(world, ctx, admin):
    _bill(ctx, admin, "2026-09", "50.00", "2026-09-25", ["l_sam"], ["50.00"])
    octo = _bill(ctx, admin, "2026-10", "50.00", "2026-10-25", ["l_sam"], ["50.00"])
    _pay(ctx, admin, octo, "l_sam", "50.00")
    snap, views = _views(ctx)
    s = summarize(views, "2026-10", TODAY)
    assert s.outstanding == D("0.00") and s.paid_count == 1
    assert s.previous_outstanding == D("50.00") and s.previous_count == 1
    sep_view = next(v for v in views if v.month == "2026-09")
    assert sep_view.status is Status.OVERDUE and sep_view.outstanding == D("50.00")


def test_multiple_lines_one_email_independent_payment_histories(world, ctx, admin, alex):
    b = _bill(ctx, admin, "2026-10", "100.00", "2026-10-25", ["l_alex1", "l_alex2"], ["60.00", "40.00"])
    _pay(ctx, admin, b, "l_alex1", "60.00")
    snap, views = _views(ctx)
    mine = visible_views(alex, views)
    assert len(mine) == 2
    l1 = line_summary(snap.line("l_alex1"), mine, "2026-10", TODAY)
    l2 = line_summary(snap.line("l_alex2"), mine, "2026-10", TODAY)
    assert l1.status is Status.PAID and l1.current_outstanding == D("0.00")
    assert l2.status is Status.DUE and l2.current_outstanding == D("40.00")   # paying one line did not settle the other
    combined = summarize(mine, "2026-10", TODAY)
    assert combined.charges == D("100.00") and combined.collected == D("60.00") and combined.outstanding == D("40.00")


def test_shared_bill_total_not_duplicated(world, ctx, admin):
    _bill(ctx, admin, "2026-10", "120.00", "2026-10-25", ["l_alex1", "l_alex2", "l_sam"], ["40.00"] * 3)
    snap, views = _views(ctx)
    assert monthly_bill_totals(snap.bills) == {"2026-10": D("120.00")}
    assert summarize(views, "2026-10", TODAY).charges == D("120.00")
    assert monthly_bill_totals(snap.bills + snap.bills) == {"2026-10": D("120.00")}  # same bill id twice -> once


# DISABLED: partial/pending payments and per-payment history were removed (a share is now simply paid or unpaid).
# def test_multiple_partial_payments_accumulate(world, ctx, admin):
#     b = _bill(ctx, admin, "2026-10", "90.00", "2026-10-25", ["l_sam"], ["90.00"])
#     _pay(ctx, admin, b, "l_sam", "30.00")
#     _, v = _views(ctx)
#     assert v[0].status is Status.PARTIAL and v[0].outstanding == D("60.00")
#     _pay(ctx, admin, b, "l_sam", "25.50")
#     _, v = _views(ctx)
#     assert v[0].paid == D("55.50") and v[0].outstanding == D("34.50")
#     _pay(ctx, admin, b, "l_sam", "34.50")
#     _, v = _views(ctx)
#     assert v[0].status is Status.PAID


# DISABLED: partial/pending payments and per-payment history were removed (a share is now simply paid or unpaid).
# def test_only_confirmed_payments_count(world, ctx, admin):
#     _bill(ctx, admin, "2026-10", "50.00", "2026-10-25", ["l_sam"], ["50.00"])
#     sid = load_snapshot(ctx.repo).shares[0].share_id
#     add_payment(ctx.repo, sid, "50.00", status="pending")
#     _, v = _views(ctx)
#     assert v[0].paid == D("0.00") and v[0].status is Status.DUE


def test_draft_bills_excluded_from_user_figures(world, ctx, admin):
    _bill(ctx, admin, "2026-10", "50.00", "2026-10-25", ["l_sam"], ["50.00"], publish=False)
    _, v = _views(ctx)
    assert v == []


def test_equal_split_sums_exactly():
    parts = equal_split(D("100.00"), 3)
    assert parts == [D("33.34"), D("33.33"), D("33.33")] and sum(parts) == D("100.00")


def test_reconcile_flags_discrepancy():
    assert reconcile(D("100.00"), [D("60"), D("40")]).ok
    r = reconcile(D("100.00"), [D("60"), D("30")])
    assert not r.ok and r.difference == D("10.00")
