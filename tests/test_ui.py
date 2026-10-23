import re
from pathlib import Path
from datetime import date, datetime, timezone
from decimal import Decimal as D

import pytest

from core.snapshot import load_snapshot
from sample_data import load_sample as load_demo
from ui import components as ui
from ui.charts import trend_chart, trend_frame
from ui.theme import CSS, TOKENS
from utils.calculations import Status, build_share_views, summarize


def test_theme_uses_only_design_tokens_and_status_cues():
    allowed = {"#0a0a0f", "#161b22", "#7c3aed", "#ffffff", "#8b949e", "#484f58",
               "#3fb950", "#d29922", "#f85149"}  # + green/yellow/red status cues (user-requested)
    found = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}\b", CSS)}
    assert found <= allowed, found - allowed
    rgbas = set(re.findall(r"rgba\((\d+),(\d+),(\d+),", CSS))
    assert rgbas <= {("255", "255", "255"), ("124", "58", "237"), ("0", "0", "0")}  # white / accent / shadow only
    assert TOKENS["accent"] == "#7C3AED"


def test_theme_is_responsive_and_accessible():
    assert "@media (max-width: 640px)" in CSS and "@media (max-width: 1024px)" in CSS
    assert ":focus-visible" in CSS and "outline: 2px solid var(--mq-accent)" in CSS
    assert "overflow-x: auto" in CSS  # tables scroll on small screens
    assert "font-weight: 500" in CSS and "Inter" in CSS


@pytest.mark.parametrize("status", list(Status))
def test_status_conveyed_by_text_not_colour_alone(status):
    html = ui.status_pill(status)
    assert status.value in html and "aria-hidden" in html  # label text plus a glyph


def test_html_escaping_prevents_injection():
    bad = '<script>alert(1)</script>'
    assert "<script>" not in ui.metric_card(bad, bad, bad)
    assert "<script>" not in ui.empty_state(bad, bad)
    assert "<script>" not in ui.notice("error", bad)
    assert "<script>" not in ui.table([bad], [[bad]])


def test_payment_table_masks_numbers_and_formats_money(repo):
    load_demo(repo, date(2026, 10, 22))
    snap = load_snapshot(repo)
    views = build_share_views(snap, date(2026, 10, 22))
    html = ui.payment_table(views, "USD")
    assert "5550100002" not in html and "•••• 0002" in html
    assert re.search(r"\$\d+\.\d{2}", html)
    for label in ("Amount due", "Paid", "Outstanding", "Due date", "Status"):
        assert label in html


def test_demo_dataset_dashboard_numbers(repo):
    load_demo(repo, date(2026, 10, 22))
    snap = load_snapshot(repo)
    assert snap.warnings == []
    today = date(2026, 10, 22)
    s = summarize(build_share_views(snap, today), snap.settings.period(datetime(2026, 10, 22, tzinfo=timezone.utc)), today)
    assert s.charges == D("215.00") and s.collected == D("106.00") and s.outstanding == D("109.00")
    assert s.previous_outstanding == D("124.00")  # Jo 20 + Alex work 40 (two months ago); Alex work 22 + Jo 42 (last month)


def test_chart_is_single_accent_smooth_line():
    spec = trend_chart({"2026-08": D("200"), "2026-09": D("210"), "2026-10": D("215")}).to_dict()
    mark = spec["mark"]
    assert mark["interpolate"] == "monotone" and mark["color"] == TOKENS["accent"]
    assert list(trend_frame({"2026-08": D("1")})["amount"]) == [1.0]


# DISABLED: these tests booted the whole app in demo mode. Demo mode was removed, and running app.py now
# reads .streamlit/secrets.toml, i.e. the REAL Google Sheet, so they must not run as they are.
# def test_app_smoke_demo_mode(monkeypatch):
#     """Boot the real Streamlit app headlessly in demo mode and walk guest -> sign in as admin."""
#     from streamlit.testing.v1 import AppTest
#
#     monkeypatch.setenv("MOBIQ_BACKEND", "demo")
#     at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30).run()
#     assert not at.exception
#     # guest path
#     next(b for b in at.button if b.label == "Continue as guest").click()
#     at.run()
#     assert not at.exception
#     assert any("Sign in to continue" in m.value for m in at.markdown)
#
#
# def _sign_in(at, email):
#     at.text_input[0].input(email)
#     next(b for b in at.button if b.label == "Sign in").click()
#     at.run()
#     return at
#
#
# def _app(monkeypatch):
#     from streamlit.testing.v1 import AppTest
#
#     monkeypatch.setenv("MOBIQ_BACKEND", "demo")
#     return AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30).run()
#
#
# def test_app_user_sign_in_sees_own_lines_only(monkeypatch):
#     at = _sign_in(_app(monkeypatch), "alex@example.com")
#     assert not at.exception
#     page = " ".join(m.value for m in at.markdown)
#     assert "Alex personal" in page and "Alex work" in page and "Sam phone" not in page
#     assert "5550100003" not in page and "5550100002" not in page  # raw numbers never rendered
#     assert "Payments made" in page and "Alex personal" in page
#     assert not any(b.label == "Admin Console" for b in at.sidebar.button)  # nav item hidden for non-admins
#
#
# def test_app_admin_sees_console_and_logout_clears_session(monkeypatch):
#     at = _sign_in(_app(monkeypatch), "admin@example.com")
#     assert not at.exception
#     assert "Sam phone" in " ".join(m.value for m in at.markdown)  # admin sees everyone
#     next(b for b in at.button if b.label == "Log out").click()
#     at.run()
#     assert not at.exception and "mq_auth" not in at.session_state  # all user state cleared


def _admin_script():
    from pages import admin_console
    from services.common import Principal
    from tests.conftest import sample_runtime

    rt = sample_runtime()
    admin_console.render(rt, Principal("u_admin", "admin@example.com", "Admin", "admin"))


def _forged_script():
    from pages import admin_console
    from services.common import Principal
    from tests.conftest import sample_runtime

    rt = sample_runtime()
    admin_console.render(rt, Principal("u_alex", "alex@example.com", "Alex", "admin"))  # claims admin; sheet says user


def test_admin_console_renders_all_tabs():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_admin_script, default_timeout=30).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Shares & payments", "Bills", "Lines & users", "Settings"]
    page = " ".join(m.value for m in at.markdown)
    assert "Billing periods" in page and "Settled" in page and "Pending" in page
    assert "$106.00" in page and "$109.00" in page  # current month: collected vs still to pay, all users
    assert not any("Create bill" in e.label for e in at.expander) and "Manage a bill" not in page  # hidden


def test_admin_console_refuses_forged_admin_principal():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_forged_script, default_timeout=30).run()
    page = " ".join(m.value for m in at.markdown)
    assert "Administrator access is required" in page and not at.tabs


def test_user_payments_made_table_monthwise(repo):
    from services.access_service import visible_views
    from services.common import Principal

    load_demo(repo, date(2026, 10, 22))
    snap = load_snapshot(repo)
    views = build_share_views(snap, date(2026, 10, 22))
    alex = Principal("u_alex", "alex@example.com", "Alex", "user")
    mine = [v for v in visible_views(alex, views) if v.paid > 0]
    html = ui.payments_made_table(mine, snap.payments, "USD")
    assert html.count("<tr>") == len(mine) + 1 and "Sam phone" not in html  # header + one row per paid month/line
    assert "Last payment" in html


def _toggle_script():
    import streamlit as st
    from pages import admin_console
    from services.common import Principal
    from tests.conftest import sample_runtime

    @st.cache_resource
    def rt():
        return sample_runtime()

    admin_console.render(rt(), Principal("u_admin", "admin@example.com", "Admin", "admin"))


def _paid_boxes(at):
    return [c for c in at.checkbox if c.key and c.key.startswith("paid_")]


def test_admin_paid_checkbox_marks_paid_and_unticking_asks_first():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_toggle_script, default_timeout=30).run()
    assert not at.exception
    boxes = _paid_boxes(at)
    assert len(boxes) == 5 and [c.value for c in boxes].count(False) == 3  # demo: 3 of 5 current shares unpaid
    next(c for c in boxes if not c.value).check().run()  # ticking applies immediately
    assert not at.exception
    assert [c.value for c in _paid_boxes(at)].count(False) == 2  # read back from the data

    paid = next(c for c in _paid_boxes(at) if c.value)
    paid.uncheck().run()  # unticking must NOT change anything yet; a confirmation dialog is shown instead
    assert not at.exception
    assert [c.value for c in _paid_boxes(at)].count(False) == 2  # box restored, no payment reversed
    assert "Are you sure you want to mark" in " ".join(m.value for m in at.markdown)

    next(b for b in at.button if b.key and b.key.startswith("unpaid_no_")).click().run()  # Cancel
    assert [c.value for c in _paid_boxes(at)].count(False) == 2
    assert not [b for b in at.button if b.key and b.key.startswith("unpaid_yes_")]  # dialog gone

    next(c for c in _paid_boxes(at) if c.value).uncheck().run()
    next(b for b in at.button if b.key and b.key.startswith("unpaid_yes_")).click().run()  # confirm
    assert not at.exception
    assert [c.value for c in _paid_boxes(at)].count(False) == 3  # now actually marked unpaid
    page = " ".join(m.value for m in at.markdown)
    assert "Record payment" not in page and "Reverse" not in page


def test_admin_notify_button_sends_reminder_in_demo():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_toggle_script, default_timeout=30).run()
    notify = [b for b in at.button if b.key and b.key.startswith("notify_")]
    assert len(notify) == 5 and any(b.disabled for b in notify)  # paid shares have nothing to notify
    next(b for b in notify if not b.disabled).click().run()
    assert not at.exception
    page = " ".join(m.value for m in at.markdown)
    assert "Reminder emailed to" in page


def test_bill_management_hidden_by_default_but_code_intact(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from pages import admin_console

    assert admin_console.SHOW_BILL_MANAGEMENT is False
    monkeypatch.setattr(admin_console, "SHOW_BILL_MANAGEMENT", True)
    at = AppTest.from_function(_admin_script, default_timeout=30).run()
    assert not at.exception
    assert any(e.label == "Create bill" for e in at.expander)
    assert "Manage a bill" in " ".join(m.value for m in at.markdown)


def test_edit_share_section_hidden_until_button_clicked():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_toggle_script, default_timeout=30).run()
    assert not at.exception
    assert not any(t.label == "Amount due" for t in at.text_input)  # hidden by default
    next(b for b in at.button if b.key == "mq_edit_share_btn").click().run()
    assert not at.exception
    assert any(t.label == "Amount due" for t in at.text_input)  # opened by the button
    next(b for b in at.button if b.key == "mq_edit_share_btn").click().run()
    assert not any(t.label == "Amount due" for t in at.text_input)  # and closes again
