from datetime import date

from core.snapshot import load_snapshot
from services import bill_service
from services.access_service import guest_view, own_lines, visible_views
from services.common import GUEST
from utils.calculations import build_share_views

TODAY = date(2026, 10, 22)


def _published(ctx, admin):
    b = bill_service.create_bill(ctx, admin, billing_month="2026-10", total_bill="100.00", due_date="2026-10-25",
                                 line_ids=["l_alex1", "l_alex2", "l_sam"])
    for lid, amt in (("l_alex1", "40.00"), ("l_alex2", "30.00"), ("l_sam", "30.00")):
        bill_service.create_share(ctx, admin, bill_id=b.bill_id, line_id=lid, amount_due=amt)
    bill_service.publish_bill(ctx, admin, b.bill_id, confirm=True)
    return build_share_views(load_snapshot(ctx.repo), TODAY)


def test_user_sees_only_own_records(world, ctx, admin, alex):
    views = _published(ctx, admin)
    mine = visible_views(alex, views)
    assert {v.share.line_id for v in mine} == {"l_alex1", "l_alex2"}
    assert len(visible_views(admin, views)) == 3


def test_guest_sees_nothing_by_default(world, ctx, admin):
    views = _published(ctx, admin)
    assert visible_views(GUEST, views) == []
    assert guest_view("none", views, "2026-10") is None


def test_guest_aggregate_has_no_names_or_per_line_amounts(world, ctx, admin):
    views = _published(ctx, admin)
    gv = guest_view("aggregate", views, "2026-10")
    assert str(gv.charges) == "100.00" and gv.total_count == 3 and gv.statuses == ()


def test_guest_status_only_is_anonymised(world, ctx, admin):
    views = _published(ctx, admin)
    gv = guest_view("status_only", views, "2026-10")
    assert [label for label, _ in gv.statuses] == ["Line 1", "Line 2", "Line 3"]
    assert gv.charges is None


def test_own_lines_includes_inactive_with_history(world, ctx, admin, alex):
    views = _published(ctx, admin)
    ctx.repo.update("MobileLines", "l_alex2", {"active": "FALSE"})
    snap = load_snapshot(ctx.repo)
    assert {x.line_id for x in own_lines(snap, alex, views)} == {"l_alex1", "l_alex2"}
    assert own_lines(snap, GUEST, views) == []
