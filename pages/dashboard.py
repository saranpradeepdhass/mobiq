"""Dashboard page. Reads only; all visibility decisions come from services.access_service."""
from __future__ import annotations

import streamlit as st

from datetime import date

from core.snapshot import load_snapshot
from services.access_service import guest_view, own_lines, visible_views
from services.common import Principal
from services.repository import StorageError
from ui import components as ui
from ui.charts import trend_chart
from utils.calculations import (
    Status, build_share_views, total_paid, monthly_bill_totals, monthly_share_totals, previous_views,
    summarize,
)
from utils.formatting import date_label, format_money, month_label


def render(rt, principal: Principal) -> None:
    try:
        snap = load_snapshot(rt.ctx.repo)
    except StorageError as exc:
        ui.show("error", f"Could not load data: {exc} Try again in a moment.")
        return
    settings, now = snap.settings, rt.ctx.now()
    today, period = settings.today(now), settings.period(now)
    cur = settings.currency
    all_views = build_share_views(snap, today)

    if principal.is_guest:
        _render_guest(snap, all_views, period, cur)
        return

    views = visible_views(principal, all_views)
    st.session_state["mq_period"] = period
    lines = own_lines(snap, principal, all_views)

    # Scope: admins can switch between everyone and their own lines.
    scope_views, selected = views, None
    if principal.is_admin:
        mine = [v for v in views if v.share.user_id == principal.user_id]
        scope = st.segmented_control("Scope", ["Everyone", "My lines"], default="Everyone",
                                     label_visibility="collapsed", key="mq_scope") or "Everyone"
        if scope == "My lines":
            scope_views = mine
    personal = (not principal.is_admin) or st.session_state.get("mq_scope") == "My lines"
    if personal and len(lines) > 1:  # selector is hidden when there is only one line
        options = ["All my lines"] + [x.line_label for x in lines]
        choice = st.selectbox("View", options, key="mq_line_select", filter_mode=None)  # pick-only, no typing
        if choice != "All my lines":
            selected = next(x for x in lines if x.line_label == choice)
            scope_views = [v for v in scope_views if v.share.line_id == selected.line_id]

    if not scope_views and not lines:
        ui.render(ui.empty_state("No bills yet", "Nothing has been published for your lines. Check back after the next bill is published."))
        _payment_instructions(settings)
        return

    s = summarize(scope_views, period, today)
    cur_due_dates = [v.due_date for v in scope_views if v.month == period]
    due_on = min(cur_due_dates) if cur_due_dates else date(int(period[:4]), int(period[5:7]), settings.default_due_day)
    balance_due = s.outstanding + s.previous_outstanding  # unpaid balance only; paid amounts are excluded
    paid_total, paid_since = total_paid(scope_views)
    ui.render(ui.grid([
        ui.metric_card("Total due", format_money(balance_due, cur),
                       f"balance across {s.outstanding_count + s.previous_count} unpaid bill(s)", focus=True),
        ui.metric_card("Due date", date_label(due_on), f"{s.paid_count} paid · {s.outstanding_count} outstanding"),
        ui.metric_card("Current Month Bill", format_money(s.charges, cur), month_label(period)),
        ui.metric_card("Amount paid so far", format_money(paid_total, cur),
                       f"since {date_label(paid_since)}" if paid_since else "no payments yet"),
    ]))
    if s.previous_outstanding > 0:
        ui.show("warning", f"{format_money(s.previous_outstanding, cur)} from earlier months is still unpaid, separate from this month's bill.")

    ui.section("Trend")
    totals = (monthly_bill_totals(snap.bills) if principal.is_admin and not personal
              else monthly_share_totals(scope_views))
    if len(totals) >= 2:
        st.altair_chart(trend_chart(totals), width="stretch")
    else:
        ui.render(ui.empty_state("Not enough history", "The trend appears once there are two or more billing months."))

    if principal.is_admin:  # admin: current-month status table (unchanged)
        ui.section(f"Payments · {month_label(period)}")
        cur_views = [v for v in scope_views if v.month == period]
        if cur_views:
            ui.render(ui.payment_table(cur_views, cur, show_user=not personal))
        else:
            ui.render(ui.empty_state("No bills this period", "No published bill covers this billing period yet."))
    else:  # users: what they have paid so far, one row per month and line
        ui.section("Payments made")
        paid_views = [v for v in scope_views if v.share.paid]
        if paid_views:
            ui.render(ui.payments_made_table(paid_views, cur))
        else:
            ui.render(ui.empty_state("No payments yet", "Shares marked paid will appear here, month by month."))

    hist = previous_views(scope_views, period)  # unpaid only; settled months are not listed
    if hist:
        ui.section("Historical dues")
        ui.render(ui.history_table(hist, cur))
    _payment_instructions(settings)
    if snap.warnings and principal.is_admin:
        with st.expander(f"Data warnings ({len(snap.warnings)})"):
            for w in snap.warnings:
                st.write(w)


def _payment_instructions(settings) -> None:
    if settings.payment_instructions:
        ui.section("How to pay")
        ui.render(f'<div class="mq-card"><div class="mq-meta-2">{ui.esc(settings.payment_instructions)}</div></div>')


def _render_guest(snap, all_views, period, cur) -> None:
    gv = guest_view(snap.settings.guest_visibility, all_views, period)
    ui.show("info", "You are browsing as a guest (read-only). Sign in to see your bills.")
    if gv is None:
        ui.render(ui.empty_state("Sign in to continue", "Financial details are private. Ask an admin for an invitation."))
        return
    if gv.policy == "aggregate":
        ui.render(ui.grid([
            ui.metric_card("Billed", format_money(gv.charges, cur), month_label(period), focus=True),
            ui.metric_card("Collected", format_money(gv.collected, cur), "all lines combined"),
            ui.metric_card("Shares paid", f"{gv.paid_count} / {gv.total_count}", "no names shown"),
        ], cols=3))
    else:
        rows = [[label, ui.status_pill(Status(status))] for label, status in gv.statuses]
        ui.render(ui.table(["Line", "Status"], rows, raw_cols={1}) if rows
                  else ui.empty_state("No bills this period", "Nothing published yet."))
