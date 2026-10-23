"""Mobiq entry point: configuration, session handling, login, navigation and header."""
from __future__ import annotations

import time

import streamlit as st

st.set_page_config(page_title="Mobiq", page_icon="📊", layout="wide", initial_sidebar_state="auto")

# Streamlit's own menu/deploy button/footer are hidden in ui/theme.py (the sidebar toggle is kept on purpose).

from core.snapshot import load_snapshot  # noqa: E402
from pages import admin_console, dashboard  # noqa: E402
from services.auth_service import RateLimited, DeliveryError, resolve_principal  # noqa: E402
from services.common import GUEST, AuthenticationError, Principal  # noqa: E402
from services.repository import StorageError  # noqa: E402
from services.runtime import Runtime, build_runtime  # noqa: E402
from ui import components as ui  # noqa: E402
from ui.theme import apply_theme  # noqa: E402
from utils.formatting import month_label  # noqa: E402
from utils.validators import ValidationError  # noqa: E402


@st.cache_resource(show_spinner="Connecting…")
def get_runtime() -> Runtime:
    return build_runtime(st.secrets)


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
        signin, guest = st.tabs(["Sign in", "Guest"])
        with signin:
            _signin_form(rt)
        with guest:
            st.write("Browse in read-only mode. What you can see is controlled by the administrators.")
            if st.button("Continue as guest"):
                st.session_state["mq_auth"] = {"guest": True}
                st.rerun()


def _email_only_form(rt: Runtime) -> None:
    with st.form("email_signin"):
        email = st.text_input("Email *", autocomplete="email", placeholder="you@example.com")
        if st.form_submit_button("Sign in", type="primary"):
            if not email.strip():
                ui.show("error", "Email is required.")
                return
            try:
                p = rt.auth.sign_in_email_only(email)
                st.session_state.clear()
                st.session_state["mq_auth"] = {"user_id": p.user_id, "at": time.time()}
                st.rerun()
            except (ValidationError, AuthenticationError, RateLimited) as exc:
                ui.show("error", str(exc))
            except StorageError:
                ui.show("error", "Sign-in is temporarily unavailable. Please try again.")
    ui.render('<div class="mq-meta">New here? Ask an administrator to invite your email address first.</div>')


def _signin_form(rt: Runtime) -> None:
    if not rt.require_code:
        _email_only_form(rt)
        return
    pending = st.session_state.get("mq_login_email")
    if rt.dev_codes:
        ui.show("warning", "Development mode: sign-in codes are shown on screen. Do not use this setting in production.")
    if not pending:
        with st.form("request_code"):
            email = st.text_input("Email", autocomplete="email")
            if st.form_submit_button("Email me a code", type="primary"):
                try:
                    msg, shown = rt.auth.request_code(email)
                    st.session_state["mq_login_email"] = email.strip().lower()
                    st.session_state["mq_login_msg"] = msg
                    st.session_state["mq_dev_code"] = shown
                    st.rerun()
                except ValidationError as exc:
                    ui.show("error", str(exc))
                except RateLimited as exc:
                    ui.show("error", str(exc))
                except (DeliveryError, StorageError):
                    ui.show("error", "We could not send a code right now. Please try again later.")
        ui.render('<div class="mq-meta">New here? Ask an administrator to invite your email address first.</div>')
        return
    ui.show("info", st.session_state.get("mq_login_msg", ""))
    if st.session_state.get("mq_dev_code"):
        ui.show("info", f"Dev code: {st.session_state['mq_dev_code']}")
    with st.form("verify_code"):
        code = st.text_input("6-digit code", max_chars=8, autocomplete="one-time-code")
        c1, c2 = st.columns(2)
        if c1.form_submit_button("Sign in", type="primary"):
            try:
                p = rt.auth.verify_code(pending, code)
                st.session_state.clear()
                st.session_state["mq_auth"] = {"user_id": p.user_id, "at": time.time()}
                st.rerun()
            except AuthenticationError as exc:
                ui.show("error", str(exc))
            except StorageError:
                ui.show("error", "Sign-in is temporarily unavailable. Please try again.")
        if c2.form_submit_button("Use a different email"):
            for k in ("mq_login_email", "mq_login_msg", "mq_dev_code"):
                st.session_state.pop(k, None)
            st.rerun()


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
