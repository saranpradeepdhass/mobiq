"""Admin Console. UI only: every action calls a service that re-checks admin rights server-side."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import streamlit as st

from core.snapshot import load_snapshot
from services import bill_service, directory_service, payment_service, reminder_service
from services.mailer import DeliveryError
from services.common import (
    AuthorizationError, BusinessRuleError, ConfirmationRequired, Principal, require_admin,
)
from services.repository import ConflictError, DuplicateError, NotFoundError, StorageError
from ui import components as ui
from utils.calculations import build_share_views
from utils.formatting import date_label, format_money, mask_phone, month_label
from utils.validators import GUEST_POLICIES, ValidationError

SHOW_BILL_MANAGEMENT = False  # UI switch: True shows "Create bill" and "Manage a bill" on the Bills tab (code kept intact)

_EXPECTED = (ValidationError, BusinessRuleError, DuplicateError, NotFoundError, ConflictError,
             AuthorizationError, ConfirmationRequired, StorageError)


def _act(fn, success: str) -> None:
    """Run a service call; show a clear message either way. Streamlit reruns after the click."""
    try:
        fn()
    except ConfirmationRequired as exc:
        ui.show("warning", f"{exc} Tick the confirmation box and submit again.")
    except ConflictError:
        ui.show("error", "Someone else changed this record. Reload and try again.")
    except _EXPECTED as exc:
        ui.show("error", str(exc))
    except Exception:
        ui.show("error", "Unexpected error; nothing was changed. Please try again.")
    else:
        st.session_state["mq_flash"] = success
        st.rerun()


def render(rt, principal: Principal) -> None:
    ctx = rt.ctx
    try:
        require_admin(ctx, principal, fresh=False)  # page guard (cached); every write re-checks with a fresh read
    except AuthorizationError:
        ui.show("error", "Administrator access is required.")
        return
    if flash := st.session_state.pop("mq_flash", None):
        ui.show("success", flash)
    try:
        snap = load_snapshot(ctx.repo)  # cached; any write clears the cache, direct sheet edits show within the TTL
    except StorageError as exc:
        ui.show("error", f"Could not load data: {exc}")
        return
    tabs = st.tabs(["Shares & payments", "Bills", "Lines & users", "Settings"])
    with tabs[0]:
        _payments(ctx, principal, snap, rt.mailer)
    with tabs[1]:
        _bills(ctx, principal, snap)
    with tabs[2]:
        _directory(ctx, principal, snap)
    with tabs[3]:
        _settings(ctx, principal, snap)


def _line_name(snap, lid):
    ln = snap.line(lid)
    owner = snap.user(ln.user_id) if ln else None
    return f"{ln.line_label} · {owner.name if owner else '?'} · {mask_phone(ln.mobile_number)}" if ln else lid


def _bills(ctx, actor, snap):
    cur = snap.settings.currency
    ui.section("Billing periods")
    if snap.bills:
        rows = []
        views = build_share_views(snap, snap.settings.today(ctx.now()), include_drafts=True)
        for b in sorted(snap.bills, key=lambda x: x.billing_month, reverse=True):
            mine = [v for v in views if v.share.bill_id == b.bill_id]
            settled = sum((min(v.paid, v.amount_due) for v in mine), Decimal("0.00"))  # confirmed, all users
            pending = sum((v.outstanding for v in mine), Decimal("0.00"))              # still to be paid, all users
            rows.append([month_label(b.billing_month), b.bill_status.title(), format_money(b.total_bill, cur),
                         format_money(settled, cur), format_money(pending, cur),
                         str(len(snap.bill_line_ids(b.bill_id))), date_label(b.bill_due_date)])
        ui.render(ui.table(["Month", "Status", "Total", "Settled", "Pending", "Lines", "Due"], rows, numeric={2, 3, 4}))
    else:
        ui.render(ui.empty_state("No bills", "No billing periods exist yet."))
    if SHOW_BILL_MANAGEMENT:
        _bill_management(ctx, actor, snap)


def _bill_management(ctx, actor, snap):
    """Create a bill, then edit / split / publish / close it. Hidden unless SHOW_BILL_MANAGEMENT is True."""
    cur = snap.settings.currency
    active_lines = [x for x in snap.lines if x.active]
    with st.expander("Create bill", expanded=not snap.bills):
        with st.form("create_bill", clear_on_submit=True):
            month = st.text_input("Billing month (YYYY-MM)", value=snap.settings.period(ctx.now()))
            total = st.text_input("Total bill", placeholder="215.00")
            period = snap.settings.period(ctx.now())
            due = st.date_input("Due date", value=date(int(period[:4]), int(period[5:7]), snap.settings.default_due_day))
            ids = st.multiselect("Mobile lines on this bill", [x.line_id for x in active_lines],
                                 format_func=lambda i: _line_name(snap, i))
            if st.form_submit_button("Create draft", type="primary"):
                _act(lambda: bill_service.create_bill(ctx, actor, billing_month=month, total_bill=total,
                                                      due_date=due, line_ids=ids), "Draft bill created.")

    if not snap.bills:
        return
    ui.section("Manage a bill")
    bid = st.selectbox("Bill", [b.bill_id for b in snap.bills], key="mq_bill_sel",
                       format_func=lambda i: f"{month_label(snap.bill(i).billing_month)} · {snap.bill(i).bill_status} · {format_money(snap.bill(i).total_bill, cur)}")
    bill = snap.bill(bid)
    rec = bill_service.bill_reconciliation(snap, bid)
    if not rec.ok:
        ui.show("warning", f"Shares total {format_money(rec.allocated, cur)} but the bill is {format_money(rec.total, cur)} "
                           f"(difference {format_money(rec.difference, cur)}).")
    if bill.bill_status == "closed":
        ui.show("info", "This bill is closed and cannot be edited. Unpaid balances stay visible as historical dues.")
        return
    with st.form(f"edit_bill_{bid}"):
        total = st.text_input("Total bill", value=str(bill.total_bill))
        due = st.date_input("Due date", value=bill.bill_due_date)
        current = snap.bill_line_ids(bid)
        ids = st.multiselect("Lines", [x.line_id for x in snap.lines if x.active or x.line_id in current],
                             default=current, format_func=lambda i: _line_name(snap, i))
        ok = st.checkbox("I confirm this change to a published bill") if bill.bill_status == "published" else False
        if st.form_submit_button("Save changes"):
            _act(lambda: bill_service.update_bill(ctx, actor, bid, total_bill=total, due_date=due, line_ids=ids,
                                                  expected_updated_at=bill.updated_at, confirm_material_change=ok),
                 "Bill updated.")
    c1, c2 = st.columns(2)
    if bill.bill_status == "draft":
        if c1.button("Split total equally across lines", key=f"split_{bid}"):
            _act(lambda: bill_service.generate_equal_shares(ctx, actor, bid), "Equal shares created.")
        with c2.form(f"publish_{bid}"):
            ok = st.checkbox("I confirm I want to publish this bill to users")
            if st.form_submit_button("Publish", type="primary"):
                _act(lambda: bill_service.publish_bill(ctx, actor, bid, confirm=ok, expected_updated_at=bill.updated_at),
                     "Bill published.")
    else:
        with c2.form(f"close_{bid}"):
            ok = st.checkbox("I confirm I want to close this billing period")
            if st.form_submit_button("Close period"):
                _act(lambda: bill_service.close_bill(ctx, actor, bid, confirm=ok), "Billing period closed.")


def _paid_changed(ctx, actor, share_id: str, key: str) -> None:
    """Checkbox on_change callback (runs once per click, so a failed update is never retried on rerun).
    Ticking marks the share paid straight away. Unticking is NOT applied here: the box is put back and a
    confirmation dialog is queued (see _confirm_unpaid_dialog)."""
    wanted = bool(st.session_state[key])
    if not wanted:
        st.session_state[key] = True  # stays ticked until the admin confirms in the dialog
        st.session_state["mq_confirm_unpaid"] = share_id
        return
    try:
        payment_service.set_share_paid(ctx, actor, share_id, True)
        st.session_state["mq_flash"] = "Marked as paid."
    except _EXPECTED as exc:
        st.session_state[key] = False  # nothing was changed
        st.session_state["mq_flash_error"] = str(exc)
    except Exception:
        st.session_state[key] = False
        st.session_state["mq_flash_error"] = "Unexpected error; nothing was changed."


@st.dialog("Mark as unpaid?", dismissible=False)  # only Yes / Cancel close it, so the choice is always explicit
def _confirm_unpaid_dialog(ctx, actor, share_id: str, who: str) -> None:
    st.write(f"Are you sure you want to mark **{who}** as **unpaid**? "
             "The change is recorded in the audit log.")
    yes, no = st.columns(2)
    if yes.button("Yes, mark unpaid", type="primary", key=f"unpaid_yes_{share_id}", width="stretch"):
        st.session_state.pop("mq_confirm_unpaid", None)
        try:
            payment_service.set_share_paid(ctx, actor, share_id, False)
            st.session_state["mq_flash"] = "Marked as unpaid."
        except _EXPECTED as exc:
            st.session_state["mq_flash_error"] = str(exc)
        except Exception:
            st.session_state["mq_flash_error"] = "Unexpected error; nothing was changed."
        st.rerun()
    if no.button("Cancel", key=f"unpaid_no_{share_id}", width="stretch"):
        st.session_state.pop("mq_confirm_unpaid", None)
        st.rerun()


def _notify(ctx, actor, share_id: str, mailer) -> None:
    """Button callback: email the owner a payment reminder for this share."""
    try:
        to = reminder_service.send_payment_reminder(ctx, actor, share_id, mailer)
        st.session_state["mq_flash"] = f"Reminder emailed to {to}."
    except DeliveryError:
        st.session_state["mq_flash_error"] = "The email could not be sent. Check the SMTP settings and try again."
    except _EXPECTED as exc:
        st.session_state["mq_flash_error"] = str(exc)
    except Exception:
        st.session_state["mq_flash_error"] = "Unexpected error; no email was sent."


def _payments(ctx, actor, snap, mailer):
    cur = snap.settings.currency
    if not snap.bills:
        ui.render(ui.empty_state("No bills", "No billing periods exist yet."))
        return
    bid = st.selectbox("Bill", [b.bill_id for b in sorted(snap.bills, key=lambda b: b.billing_month, reverse=True)],
                       key="mq_pay_bill", format_func=lambda i: f"{month_label(snap.bill(i).billing_month)} · {snap.bill(i).bill_status}")
    bill = snap.bill(bid)
    today = snap.settings.today(ctx.now())
    views = [v for v in build_share_views(snap, today, include_drafts=True) if v.share.bill_id == bid]
    ui.section("Shares")
    if flash := st.session_state.pop("mq_flash_error", None):
        ui.show("error", flash)
    if views:
        with st.container(key="mq_shares"):  # CSS keeps these columns in one scrollable row on phones
            widths = [2, 2, 1.5, 1.5, 1.9, 1.8, 1.6, 0.9, 1.3]
            head = st.columns(widths, vertical_alignment="center")
            for col, label in zip(head, ["User", "Line", "Amount due", "Amount paid", "Outstanding", "Due date", "Status", "Paid", "Notify"]):
                col.markdown(f"<span class='mq-label'>{label}</span>", unsafe_allow_html=True)
            for v in views:
                sid = v.share.share_id
                row = st.columns(widths, vertical_alignment="center")
                row[0].write(v.user.name if v.user else "—")
                row[1].write(v.line.line_label if v.line else "—")
                row[2].write(format_money(v.amount_due, cur))
                row[3].write(format_money(v.paid, cur))
                row[4].write(format_money(v.outstanding, cur))
                row[5].write(date_label(v.due_date))
                row[6].markdown(ui.status_pill(v.status), unsafe_allow_html=True)
                key = f"paid_{sid}_{v.paid}"  # key changes with the stored state so the box always reflects the data
                st.session_state.setdefault(key, v.outstanding == 0)  # seeded here so callbacks may also set it
                row[7].checkbox("Paid", key=key, label_visibility="collapsed",
                                disabled=bill.bill_status == "draft", on_change=_paid_changed, args=(ctx, actor, sid, key))
                row[8].button("Notify", key=f"notify_{sid}", disabled=bill.bill_status == "draft" or v.outstanding == 0,
                              on_click=_notify, args=(ctx, actor, sid, mailer), help="Email a payment reminder with the due details")
        if pending := st.session_state.get("mq_confirm_unpaid"):  # stays set until Yes or Cancel is pressed
            target = next((v for v in views if v.share.share_id == pending), None)
            if target is not None:
                who = f"{target.user.name if target.user else 'this user'} ({target.line.line_label if target.line else 'line'})"
                _confirm_unpaid_dialog(ctx, actor, pending, who)
        if bill.bill_status == "draft":
            ui.render('<div class="mq-meta">Publish the bill to enable the Paid and Notify controls.</div>')
    else:
        ui.render(ui.empty_state("No shares", "Add shares below or split the total equally on the Bills tab."))

    missing = [lid for lid in snap.bill_line_ids(bid) if not any(s.bill_id == bid and s.line_id == lid for s in snap.shares)]
    if missing and bill.bill_status != "closed":
        add_key = "mq_add_share_open"  # hidden until the admin clicks the button
        add_open = st.session_state.get(add_key, False)
        st.button("Close add share" if add_open else "Add share", key="mq_add_share_btn",
                  on_click=lambda: st.session_state.update({add_key: not add_open}))
        if add_open:
            def add_share():
                bill_service.create_share(ctx, actor, bill_id=bid, line_id=lid, amount_due=amt,
                                          confirm_material_change=ok)
                st.session_state[add_key] = False  # collapse again after a successful save

            with st.form(f"new_share_{bid}", clear_on_submit=True):
                lid = st.selectbox("Line", missing, format_func=lambda i: _line_name(snap, i))
                amt = st.text_input("Amount due")
                ok = st.checkbox("I confirm adding a share to a published bill") if bill.bill_status == "published" else False
                if st.form_submit_button("Save share", type="primary"):
                    _act(add_share, "Share added.")
    if not views:
        return
    if bill.bill_status == "closed":
        return
    open_key = "mq_edit_share_open"  # the edit section is hidden until the admin clicks the button
    is_open = st.session_state.get(open_key, False)
    st.button("Close edit share" if is_open else "Edit share", key="mq_edit_share_btn",
              on_click=lambda: st.session_state.update({open_key: not is_open}))
    if not is_open:
        return
    sid_fmt = lambda i: f"{next(v for v in views if v.share.share_id == i).line.line_label} · due {format_money(next(v for v in views if v.share.share_id == i).amount_due, cur)}"
    sid = st.selectbox("Share to edit", [v.share.share_id for v in views], format_func=sid_fmt, key=f"mq_share_{bid}")
    share = snap.share(sid)

    def update():
        bill_service.update_share(ctx, actor, sid, amount_due=amt, expected_updated_at=share.updated_at,
                                  confirm_material_change=ok)
        st.session_state[open_key] = False  # collapse again after a successful save

    with st.form(f"edit_share_{sid}"):
        amt = st.text_input("Amount due", value=str(share.amount_due))
        ok = st.checkbox("I confirm this change to a published share") if bill.bill_status == "published" else False
        if st.form_submit_button("Update share"):
            _act(update, "Share updated.")


def _directory(ctx, actor, snap):
    ui.section("Users")
    rows = [[u.name, u.email, u.role, "Active" if u.active else "Inactive",
             str(sum(1 for x in snap.lines if x.user_id == u.user_id and x.active))] for u in snap.users]
    ui.render(ui.table(["Name", "Email", "Role", "Account", "Active lines"], rows) if rows
              else ui.empty_state("No users", "Invite the first user below."))
    with st.expander("Invite user"):
        with st.form("invite", clear_on_submit=True):
            name = st.text_input("Name")
            email = st.text_input("Email")
            role = st.selectbox("Role", ["user", "admin"])
            if st.form_submit_button("Invite", type="primary"):
                _act(lambda: directory_service.invite_user(ctx, actor, name=name, email=email, role=role),
                     "User invited. They can now sign in with an emailed code.")
    if snap.users:
        with st.expander("Edit user"):
            uid = st.selectbox("User", [u.user_id for u in snap.users], format_func=lambda i: snap.user(i).name, key="mq_edit_user")
            u = snap.user(uid)
            with st.form(f"edit_user_{uid}"):
                name = st.text_input("Name", value=u.name)
                role = st.selectbox("Role", ["user", "admin"], index=0 if u.role == "user" else 1)
                active = st.checkbox("Active", value=u.active)
                if st.form_submit_button("Save"):
                    _act(lambda: directory_service.update_user(ctx, actor, uid, name=name, role=role, active=active), "User updated.")

    ui.section("Mobile lines")
    rows = [[x.line_label, snap.user(x.user_id).name if snap.user(x.user_id) else "?", mask_phone(x.mobile_number),
             x.account_reference or "—", "Active" if x.active else "Inactive"] for x in snap.lines]
    ui.render(ui.table(["Label", "Owner", "Number", "Account ref", "Status"], rows) if rows
              else ui.empty_state("No lines", "Add a line for a user."))
    with st.expander("Add line"):
        with st.form("add_line", clear_on_submit=True):
            owner = st.selectbox("Owner", [u.user_id for u in snap.users if u.active], format_func=lambda i: snap.user(i).name)
            label = st.text_input("Label", placeholder="Alex personal")
            number = st.text_input("Mobile number")
            ref = st.text_input("Account reference (optional)")
            if st.form_submit_button("Add line", type="primary"):
                _act(lambda: directory_service.add_line(ctx, actor, user_id=owner, line_label=label,
                                                        mobile_number=number, account_reference=ref), "Line added.")
    if snap.lines:
        with st.expander("Edit or deactivate line"):
            lid = st.selectbox("Line", [x.line_id for x in snap.lines], format_func=lambda i: _line_name(snap, i), key="mq_edit_line")
            ln = snap.line(lid)
            with st.form(f"edit_line_{lid}"):
                label = st.text_input("Label", value=ln.line_label)
                number = st.text_input("Mobile number (changing keeps all history)", value=ln.mobile_number)
                ref = st.text_input("Account reference", value=ln.account_reference)
                if st.form_submit_button("Save line"):
                    _act(lambda: directory_service.update_line(ctx, actor, lid, line_label=label, mobile_number=number,
                                                               account_reference=ref, expected_updated_at=ln.updated_at), "Line updated.")
            if st.button("Deactivate line" if ln.active else "Activate line", key=f"tog_{lid}"):
                _act(lambda: directory_service.set_line_active(ctx, actor, lid, not ln.active),
                     "Line deactivated; history preserved." if ln.active else "Line activated.")


def _settings(ctx, actor, snap):
    s = snap.settings
    with st.form("settings"):
        app_name = st.text_input("App name", value=s.app_name)
        currency = st.text_input("Currency (ISO code)", value=s.currency)
        tz = st.text_input("Timezone (IANA)", value=s.timezone)
        period = st.text_input("Current billing period (YYYY-MM, blank = this month)", value=s.current_billing_period)
        due_day = st.number_input("Default due day of month", 1, 28, value=s.default_due_day)
        guest = st.selectbox("Guest visibility", list(GUEST_POLICIES), index=GUEST_POLICIES.index(s.guest_visibility),
                             help="none: nothing. aggregate: combined totals only. status_only: anonymised line statuses.")
        instr = st.text_area("Payment instructions", value=s.payment_instructions, max_chars=500)
        if st.form_submit_button("Save settings", type="primary"):
            def save():
                for k, v in [("app_name", app_name), ("currency", currency), ("timezone", tz),
                             ("current_billing_period", period), ("default_due_day", int(due_day)),
                             ("guest_visibility", guest), ("payment_instructions", instr)]:
                    directory_service.update_setting(ctx, actor, k, str(v))
            _act(save, "Settings saved.")
    ui.render('<div class="mq-meta">Reminders and PDF import are extension points (see README); nothing is sent or published automatically.</div>')
