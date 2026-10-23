"""Builds the repository, service context and auth service from configuration (Streamlit secrets)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from services.auth_service import AuthService, DevScreenSender, SmtpSender
from services.common import ServiceContext
from services.google_sheets_service import GoogleSheetsRepository
from services.mailer import Mailer, SmtpMailer
from services.repository import Repository, StorageError


@dataclass
class Runtime:
    ctx: ServiceContext
    auth: AuthService
    dev_codes: bool
    require_code: bool
    session_hours: float
    mailer: Mailer | None = None  # None: email not configured (payment reminders unavailable)


def _get(cfg: Mapping, section: str) -> Mapping:
    try:
        return cfg[section]
    except (KeyError, FileNotFoundError):
        return {}


def _smtp_mailer(smtp: Mapping) -> SmtpMailer:
    return SmtpMailer(smtp["host"], smtp.get("port", 587), smtp.get("username", ""), smtp.get("password", ""),
                      smtp["sender"], bool(smtp.get("use_ssl", False)))


def build_runtime(cfg: Mapping, *, repo: Repository | None = None, mailer: Mailer | None = None) -> Runtime:
    """cfg behaves like st.secrets. `repo` / `mailer` may be injected (tests); otherwise Google Sheets and SMTP
    are built from the configuration."""
    auth_cfg = _get(cfg, "auth")
    if repo is None:
        gs = _get(cfg, "gsheets")
        if not gs:
            raise StorageError("Google Sheets is not configured. Add [gsheets] to .streamlit/secrets.toml.")
        repo = GoogleSheetsRepository.from_config(gs, ttl=float(gs.get("cache_ttl_seconds", 30)))
    ctx = ServiceContext(repo)

    smtp_cfg = _get(cfg, "smtp")
    require_code = bool(auth_cfg.get("require_code", False))
    dev = require_code and bool(auth_cfg.get("dev_show_code", False))
    if not require_code or dev:
        sender = DevScreenSender()  # email-only mode issues no codes; dev mode shows them on screen
    else:
        if not smtp_cfg:
            raise StorageError("[smtp] is not configured; cannot send sign-in codes.")
        sender = SmtpSender(_smtp_mailer(smtp_cfg))
    secret = str(auth_cfg.get("secret", ""))
    if not secret:
        raise StorageError("[auth] secret is not configured. Add a long random string to .streamlit/secrets.toml.")
    auth = AuthService(ctx, sender, secret, ttl_seconds=int(auth_cfg.get("code_ttl_seconds", 600)),
                       bootstrap_admin_email=str(auth_cfg.get("bootstrap_admin_email", "")))
    if mailer is None and smtp_cfg:
        mailer = _smtp_mailer(smtp_cfg)
    return Runtime(ctx, auth, dev, require_code, float(auth_cfg.get("session_hours", 8)), mailer)
