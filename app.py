"""Mobiq entry point: configuration, session handling, login, navigation and header."""
from __future__ import annotations

import time

import streamlit as st

st.set_page_config(page_title="Mobiq", page_icon="📲", layout="wide", initial_sidebar_state="auto")

# Streamlit's own menu/deploy button/footer are hidden in ui/theme.py (the sidebar toggle is kept on purpose).

SHOW_GUEST_LOGIN = False  # True shows the read-only "Guest" tab on the login screen (code kept; guests see nothing by default)

from core.snapshot import load_snapshot  # noqa: E402
from pages import admin_console, dashboard  # noqa: E402
from services.auth_service import RateLimited, DeliveryError, resolve_principal  # noqa: E402
from services.common import GUEST, AuthenticationError, Principal  # noqa: E402
from services.config import load_config  # noqa: E402
from services.repository import DuplicateError, StorageError  # noqa: E402
from services.runtime import Runtime, build_runtime  # noqa: E402
from ui import components as ui  # noqa: E402
from ui.theme import apply_theme  # noqa: E402
from utils.formatting import month_label  # noqa: E402
from utils.validators import ValidationError  # noqa: E402


@st.cache_resource(show_spinner="Connecting…")
def get_runtime() -> Runtime:
    return build_runtime(load_config(st.secrets))


def logout() -> None:
    """Button callback: clear all user-specific session state (the shared runtime/cache is untouched)."""
    for key in list(st.session_state.keys()):
        if not str(key).startswith("$$"):  # leave Streamlit's own internal bookkeeping alone
            del st.session_state[key]


def current_principal(rt: Runtime) -> Principal | None:
    auth = st.session_state.get("mq_auth")
    if not auth:
        return None
    if auth.get("guest"):
        if not SHOW_GUEST_LOGIN:  # a stale guest session must not survive while the feature is hidden
            st.session_state.clear()
            return None
        return GUEST
    if time.time() - auth["at"] > rt.session_hours * 3600:
        st.session_state.clear()
        return None
    # Role and active flag are re-read from storage on every run; the session only carries the id.
    p = resolve_principal(rt.ctx, auth["user_id"])
    if p is None:
        st.session_state.clear()
    return p


def login_view(rt: Runtime) -> None:
    ui.render('<div class="mq-brand" style="font-size:2rem">Mobi<b>q</b></div>'
              '<div class="mq-sub" style="margin-bottom:1.5rem">Mobile bill splitting, simplified.</div>')
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        if not SHOW_GUEST_LOGIN:
            _signin_form(rt)
            return
        signin, guest = st.tabs(["Sign in", "Guest"])
        with signin:
            _signin_form(rt)
        with guest:
            st.write("Browse in read-only mode. What you can see is controlled by the administrators.")
            if st.button("Continue as guest"):
                st.session_state["mq_auth"] = {"guest": True}
                st.rerun()


_LOGIN_KEYS = ("mq_login", "mq_login_msg", "mq_dev_code")
_LOGIN_ERRORS = (ValidationError, AuthenticationError, RateLimited, DuplicateError)


def _finish_login(p: Principal) -> None:
    st.session_state.clear()
    st.session_state["mq_auth"] = {"user_id": p.user_id, "at": time.time()}
    st.rerun()


def _reset_login() -> None:
    for k in _LOGIN_KEYS:
        st.session_state.pop(k, None)


def _go(stage: str, **extra) -> None:
    """Move the sign-in wizard to another step (state lives only in this browser session)."""
    st.session_state["mq_login"] = {**st.session_state.get("mq_login", {}), "stage": stage, **extra}
    st.rerun()


def _signin_form(rt: Runtime) -> None:
    """Steps: email -> [mobile (new email)] -> [code (when require_code)] -> [PIN (admins)]."""
    state = st.session_state.get("mq_login", {"stage": "email"})
    stage, email = state["stage"], state.get("email", "")
    if rt.dev_codes:
        ui.show("warning", "Development mode: sign-in codes are shown on screen. Do not use this setting in production.")
    try:
        if stage == "email":
            _step_email(rt)
        elif stage == "mobile":
            _step_mobile(rt, email)
        elif stage == "code":
            _step_code(rt, email, state.get("is_admin", False))
        elif stage == "pin":
            _step_pin(rt, email)
    except StorageError:
        ui.show("error", "Sign-in is temporarily unavailable. Please try again.")
    except DeliveryError:
        ui.show("error", "We could not send the email right now. Please try again later.")


def _step_email(rt: Runtime) -> None:
    with st.form("login_email"):
        email = st.text_input("Email *", autocomplete="email", placeholder="you@example.com")
        go = st.form_submit_button("Continue", type="primary")
    if not go:
        ui.render('<div class="mq-meta">New here? Enter your email and we will ask for your mobile number.</div>')
        return
    if not email.strip():
        ui.show("error", "Email is required.")
        return
    try:
        status = rt.auth.account_status(email)
        clean = email.strip().lower()
        if status == "inactive":
            ui.show("error", "This account is disabled. Ask an administrator for help.")
        elif status == "new":
            if not rt.auth.allow_registration:
                ui.show("error", "We couldn't sign you in with that email. Ask an administrator to invite you.")
            else:
                _go("mobile", email=clean)
        elif rt.require_code:
            msg, shown = rt.auth.request_code(clean)
            st.session_state["mq_login_msg"], st.session_state["mq_dev_code"] = msg, shown
            _go("code", email=clean, is_admin=(status == "admin"))
        elif status == "admin":
            _go("pin", email=clean)
        else:
            _finish_login(rt.auth.sign_in_email_only(clean))
    except _LOGIN_ERRORS as exc:
        ui.show("error", str(exc))


def _step_mobile(rt: Runtime, email: str) -> None:
    ui.show("info", f"{email} is new here. Enter your mobile number to create your account.")
    with st.form("login_mobile"):
        mobile = st.text_input("Mobile number *", autocomplete="tel", placeholder="e.g. 5551234567")
        create = st.form_submit_button("Create account", type="primary")
        back = st.form_submit_button("Use a different email")
    if back:
        _reset_login()
        st.rerun()
    if not create:
        return
    if not mobile.strip():
        ui.show("error", "Mobile number is required.")
        return
    try:
        if rt.require_code:
            msg, shown = rt.auth.request_registration_code(email, mobile)
            st.session_state["mq_login_msg"], st.session_state["mq_dev_code"] = msg, shown
            _go("code", email=email, is_admin=False)
        else:
            _finish_login(rt.auth.register(email, mobile))
    except _LOGIN_ERRORS as exc:
        ui.show("error", str(exc))


def _step_code(rt: Runtime, email: str, is_admin: bool) -> None:
    ui.show("info", st.session_state.get("mq_login_msg", "") or f"A sign-in code was sent to {email}.")
    if st.session_state.get("mq_dev_code"):
        ui.show("info", f"Dev code: {st.session_state['mq_dev_code']}")
    with st.form("login_code"):
        code = st.text_input("6-digit code *", max_chars=8, autocomplete="one-time-code")
        pin = st.text_input("Admin PIN *", type="password", max_chars=4) if is_admin else None
        go = st.form_submit_button("Sign in", type="primary")
        back = st.form_submit_button("Use a different email")
    if back:
        _reset_login()
        st.rerun()
    if not go:
        return
    try:
        _finish_login(rt.auth.verify_code(email, code, admin_pin=pin))
    except _LOGIN_ERRORS as exc:
        ui.show("error", str(exc))


def _step_pin(rt: Runtime, email: str) -> None:
    ui.show("info", f"{email} is an administrator account. Enter the 4-digit admin PIN.")
    with st.form("login_pin"):
        pin = st.text_input("Admin PIN *", type="password", max_chars=4, autocomplete="off")
        go = st.form_submit_button("Sign in", type="primary")
        back = st.form_submit_button("Use a different email")
    if back:
        _reset_login()
        st.rerun()
    if not go:
        return
    if not pin.strip():
        ui.show("error", "PIN is required.")
        return
    try:
        _finish_login(rt.auth.sign_in_email_only(email, admin_pin=pin))
    except _LOGIN_ERRORS as exc:
        ui.show("error", str(exc))


def lambda_free_login(rt: Runtime):
    def login_page():
        login_view(rt)

    return login_page


def header(rt: Runtime, principal: Principal, title: str) -> None:
    try:
        period = load_snapshot(rt.ctx.repo).settings.period(rt.ctx.now())
    except StorageError:
        period = ""
    left, mid, right = st.columns([3, 4, 3], vertical_alignment="center")
    with left:
        who = ("Guest", "read-only") if principal.is_guest else (principal.name, principal.role.title())
        ui.render(f'<div class="mq-title">{ui.esc(title)}</div>'
                  f'<div class="mq-user"><span class="mq-user-name">{ui.esc(who[0])}</span>'
                  f'<span class="mq-user-role">{ui.esc(who[1])}</span></div>')
    with mid:
        if period:
            ui.render(f'<div style="text-align:center"><span class="mq-chip">Billing period <strong>{ui.esc(month_label(period))}</strong></span></div>')
    with right:
        _, slot = st.columns([1, 1])
        slot.button("Exit guest mode" if principal.is_guest else "Log out", key="mq_logout", on_click=logout, width="stretch")


def main() -> None:
    apply_theme()
    try:
        rt = get_runtime()
    except StorageError as exc:
        ui.show("error", f"Configuration problem: {exc}")
        st.stop()
    principal = current_principal(rt)
    if principal is None:
        # Always register a navigation, even for the login screen, so Streamlit never keeps a stale
        # page from a previous session (e.g. /dashboard) after logout.
        st.navigation([st.Page(lambda_free_login(rt), title="Sign in", url_path="login", default=True)],
                      position="hidden").run()
        return

    def dashboard_page():
        header(rt, principal, "Dashboard")
        dashboard.render(rt, principal)

    def admin_page():
        header(rt, principal, "Admin Console")
        admin_console.render(rt, principal)

    dash = st.Page(dashboard_page, title="Dashboard", icon=":material/dashboard:", url_path="dashboard",
                   default=not principal.is_admin)
    pages = [dash]
    if principal.is_admin:  # navigation item only for admins; the page and services re-check server-side
        # Admins land on the Admin Console after sign-in.
        pages.insert(0, st.Page(admin_page, title="Admin Console", icon=":material/admin_panel_settings:",
                                url_path="admin", default=True))
    with st.sidebar:
        ui.render('<div class="mq-brand">Mobi<b>q</b></div>')
    nav = st.navigation(pages)
    nav.run()


main()
