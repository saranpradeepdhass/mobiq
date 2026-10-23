from datetime import date
from decimal import Decimal as D

import pytest

from core.snapshot import load_snapshot
from services import bill_service, directory_service, payment_service
from services.common import (
    GUEST, AuthorizationError, BusinessRuleError, ConfirmationRequired, Principal,
)
from services.repository import DuplicateError, StorageError
from utils.calculations import build_share_views
from utils.validators import ValidationError
from tests.conftest import add_user


def make_bill(ctx, admin, lines=("l_alex1", "l_sam"), total="100.00", shares=("60.00", "40.00"), month="2026-10"):
    b = bill_service.create_bill(ctx, admin, billing_month=month, total_bill=total, due_date="2026-10-25", line_ids=list(lines))
    for lid, amt in zip(lines, shares):
        bill_service.create_share(ctx, admin, bill_id=b.bill_id, line_id=lid, amount_due=amt)
    return b


def share_id(ctx, bill, line):
    return next(s.share_id for s in load_snapshot(ctx.repo).shares if s.bill_id == bill.bill_id and s.line_id == line)


# ---- duplicates, invalid amounts -------------------------------------------------
def test_duplicate_share_prevented(world, ctx, admin):
    b = make_bill(ctx, admin)
    with pytest.raises(DuplicateError):
        bill_service.create_share(ctx, admin, bill_id=b.bill_id, line_id="l_alex1", amount_due="1.00")


def test_duplicate_bill_for_same_lines_prevented(world, ctx, admin):
    make_bill(ctx, admin)
    with pytest.raises(DuplicateError):
        bill_service.create_bill(ctx, admin, billing_month="2026-10", total_bill="5", due_date="2026-10-25",
                                 line_ids=["l_alex1", "l_sam"])


def test_share_user_comes_from_line_not_client(world, ctx, admin):
    b = make_bill(ctx, admin)
    snap = load_snapshot(ctx.repo)
    assert snap.share(share_id(ctx, b, "l_sam")).user_id == "u_sam"


def test_cannot_mark_paid_on_draft_bill(world, ctx, admin):
    b = make_bill(ctx, admin)
    with pytest.raises(BusinessRuleError):
        payment_service.set_share_paid(ctx, admin, share_id(ctx, b, "l_sam"), True)


# ---- publishing, confirmation, reconciliation -------------------------------------
def test_publish_requires_confirmation_and_reconciliation(world, ctx, admin):
    b = make_bill(ctx, admin, shares=("60.00", "30.00"))
    with pytest.raises(ConfirmationRequired):
        bill_service.publish_bill(ctx, admin, b.bill_id)
    with pytest.raises(BusinessRuleError, match="reconcile"):
        bill_service.publish_bill(ctx, admin, b.bill_id, confirm=True)
    rec = bill_service.bill_reconciliation(load_snapshot(ctx.repo), b.bill_id)
    assert rec.difference == D("10.00")


def test_material_change_to_published_bill_needs_confirmation(world, ctx, admin):
    b = make_bill(ctx, admin)
    bill_service.publish_bill(ctx, admin, b.bill_id, confirm=True)
    with pytest.raises(ConfirmationRequired):
        bill_service.update_bill(ctx, admin, b.bill_id, total_bill="110.00")
    bill_service.update_bill(ctx, admin, b.bill_id, total_bill="110.00", confirm_material_change=True)
    assert load_snapshot(ctx.repo).bill(b.bill_id).total_bill == D("110.00")


def test_closed_bill_is_frozen_but_dues_remain(world, ctx, admin):
    b = make_bill(ctx, admin)
    bill_service.publish_bill(ctx, admin, b.bill_id, confirm=True)
    bill_service.close_bill(ctx, admin, b.bill_id, confirm=True)
    with pytest.raises(BusinessRuleError):
        bill_service.update_bill(ctx, admin, b.bill_id, total_bill="1.00")
    snap = load_snapshot(ctx.repo)
    views = build_share_views(snap, date(2026, 11, 5))
    assert sum(v.outstanding for v in views) == D("100.00")  # still owed after close


def test_concurrent_edit_detected(world, ctx, admin):
    from services.repository import ConflictError
    b = make_bill(ctx, admin)
    bill_service.update_bill(ctx, admin, b.bill_id, total_bill="101.00")
    with pytest.raises(ConflictError):
        bill_service.update_bill(ctx, admin, b.bill_id, total_bill="102.00", expected_updated_at=b.updated_at)


# ---- paid flag ---------------------------------------------------------------------
def test_mark_paid_and_unpaid_sets_flag_date_and_audit(world, ctx, admin):
    b = make_bill(ctx, admin)
    bill_service.publish_bill(ctx, admin, b.bill_id, confirm=True)
    sid = share_id(ctx, b, "l_sam")
    payment_service.set_share_paid(ctx, admin, sid, True)
    payment_service.set_share_paid(ctx, admin, sid, True)  # second call changes nothing (and is not audited twice)
    share = load_snapshot(ctx.repo).share(sid)
    assert share.paid and share.paid_date == date(2026, 10, 22)
    views = build_share_views(load_snapshot(ctx.repo), date(2026, 10, 22))
    assert next(v for v in views if v.share.share_id == sid).outstanding == D("0.00")
    payment_service.set_share_paid(ctx, admin, sid, False)
    share = load_snapshot(ctx.repo).share(sid)
    assert not share.paid and share.paid_date is None
    actions = [r["action"] for r in ctx.repo.list("AuditLog")]
    assert actions.count("share.paid") == 1 and actions.count("share.unpaid") == 1


def test_paid_share_amount_cannot_be_edited_until_unpaid(world, ctx, admin):
    b = make_bill(ctx, admin)
    bill_service.publish_bill(ctx, admin, b.bill_id, confirm=True)
    sid = share_id(ctx, b, "l_sam")
    payment_service.set_share_paid(ctx, admin, sid, True)
    with pytest.raises(BusinessRuleError, match="unpaid"):
        bill_service.update_share(ctx, admin, sid, amount_due="45.00", confirm_material_change=True)
    payment_service.set_share_paid(ctx, admin, sid, False)
    bill_service.update_share(ctx, admin, sid, amount_due="45.00", confirm_material_change=True)
    assert load_snapshot(ctx.repo).share(sid).amount_due == D("45.00")


# ---- lines: history preserved ------------------------------------------------------
def test_number_change_and_deactivation_preserve_history(world, ctx, admin):
    b = make_bill(ctx, admin)
    bill_service.publish_bill(ctx, admin, b.bill_id, confirm=True)
    sid = share_id(ctx, b, "l_alex1")
    directory_service.update_line(ctx, admin, "l_alex1", mobile_number="555-010-9999")
    directory_service.set_line_active(ctx, admin, "l_alex1", False)
    snap = load_snapshot(ctx.repo)
    assert snap.line("l_alex1").mobile_number == "5550109999" and not snap.line("l_alex1").active
    assert snap.share(sid).line_id == "l_alex1"
    assert [v.share.share_id for v in build_share_views(snap, date(2026, 10, 22))].count(sid) == 1
    with pytest.raises(BusinessRuleError):
        bill_service.create_share(ctx, admin, bill_id=b.bill_id, line_id="l_alex1", amount_due="1",
                                     confirm_material_change=True)  # inactive
    details = " ".join(r["details"] for r in ctx.repo.list("AuditLog"))
    assert "5550109999" not in details and "5550100002" not in details  # audit log never holds full numbers


def test_duplicate_active_number_and_label_rejected(world, ctx, admin):
    with pytest.raises(DuplicateError):
        directory_service.add_line(ctx, admin, user_id="u_sam", line_label="Other", mobile_number="5550100002")
    with pytest.raises(DuplicateError):
        directory_service.add_line(ctx, admin, user_id="u_alex", line_label="alex work", mobile_number="5550100099")
    line = directory_service.add_line(ctx, admin, user_id="u_alex", line_label="Alex tablet", mobile_number="5550100099")
    assert line.user_id == "u_alex"  # one user, three lines


# ---- authorization --------------------------------------------------------------------
def test_non_admin_and_guest_cannot_mutate(world, ctx, alex):
    for actor in (alex, GUEST, None):
        with pytest.raises(AuthorizationError):
            bill_service.create_bill(ctx, actor, billing_month="2026-10", total_bill="1", due_date="2026-10-25", line_ids=["l_sam"])
        with pytest.raises(AuthorizationError):
            directory_service.invite_user(ctx, actor, name="X", email="x@example.com")
        with pytest.raises(AuthorizationError):
            payment_service.set_share_paid(ctx, actor, "s", True)


def test_forged_admin_principal_is_rejected(world, ctx):
    forged = Principal("u_alex", "alex@example.com", "Alex", "admin")  # client claims admin; storage says user
    with pytest.raises(AuthorizationError):
        directory_service.invite_user(ctx, forged, name="X", email="x@example.com")


def test_demoted_or_deactivated_admin_loses_access_immediately(world, ctx, admin):
    add_user(ctx.repo, "u_admin2", "admin2@example.com", "admin")
    directory_service.update_user(ctx, admin, "u_admin2", active=False)
    stale = Principal("u_admin2", "admin2@example.com", "A2", "admin")
    with pytest.raises(AuthorizationError):
        bill_service.create_bill(ctx, stale, billing_month="2026-10", total_bill="1", due_date="2026-10-25", line_ids=["l_sam"])


def test_cannot_remove_last_admin_or_self(world, ctx, admin):
    with pytest.raises(BusinessRuleError):
        directory_service.update_user(ctx, admin, "u_admin", role="user")
    with pytest.raises(BusinessRuleError):
        directory_service.update_user(ctx, admin, "u_admin", active=False)


def test_invite_duplicate_email_rejected(world, ctx, admin):
    with pytest.raises(DuplicateError):
        directory_service.invite_user(ctx, admin, name="Dup", email="ALEX@example.com")


# ---- robustness ----------------------------------------------------------------------
def test_malformed_rows_are_skipped_with_warnings(world, ctx):
    world.append("Bills", {"bill_id": "bad", "billing_month": "2026-99", "total_bill": "x", "bill_due_date": "no",
                           "bill_status": "draft"})
    world.append("BillShares", {"share_id": "orphan", "bill_id": "nope", "line_id": "l_sam", "user_id": "u_sam", "amount_due": "5"})
    snap = load_snapshot(world)
    assert snap.bills == [] and snap.shares == []
    assert any("Bills row" in w for w in snap.warnings) and any("orphan" in w for w in snap.warnings)


def test_api_failure_surfaces_as_storage_error(world, ctx, admin):
    world.fail_with = StorageError("boom")
    with pytest.raises(StorageError):
        load_snapshot(world)


def test_invalid_setting_falls_back_and_update_validates(world, ctx, admin):
    with pytest.raises(ValidationError):
        directory_service.update_setting(ctx, admin, "guest_visibility", "everything")
    with pytest.raises(ValidationError):
        directory_service.update_setting(ctx, admin, "timezone", "Mars/Base")
    directory_service.update_setting(ctx, admin, "guest_visibility", "aggregate")
    assert load_snapshot(ctx.repo).settings.guest_visibility == "aggregate"


def test_page_guard_may_use_cache_but_writes_always_read_fresh(world, ctx, admin):
    from services.common import require_admin

    seen = []
    original = world.get
    world.get = lambda table, rid, *, fresh=False: (seen.append(fresh), original(table, rid, fresh=fresh))[1]
    require_admin(ctx, admin, fresh=False)           # read-only page guard: cached read allowed
    bill_service.create_bill(ctx, admin, billing_month="2026-10", total_bill="1", due_date="2026-10-20",
                             line_ids=["l_sam"])     # a write: must re-check the role with a fresh read
    assert seen[0] is False and seen[1:] == [True]
