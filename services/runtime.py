"""Builds the repository, service context and auth service from configuration (Streamlit secrets or env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from services.auth_service import AuthService, DevScreenSender, SmtpSender
from services.mailer import Mailer, OutboxMailer, SmtpMailer
from services.common import ServiceContext
from services.demo_data import load_demo
from services.google_sheets_service import GoogleSheetsRepository
from services.repository import InMemoryRepository, Repository, StorageError


@dataclass
class Runtime:
    ctx: ServiceContext
    auth: AuthService
    demo: bool
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


def build_runtime(cfg: Mapping) -> Runtime:
    """cfg behaves like st.secrets. MOBIQ_BACKEND=demo env var also selects demo mode."""
    mobiq, auth_cfg = _get(cfg, "mobiq"), _get(cfg, "auth")
    backend = os.environ.get("MOBIQ_BACKEND") or mobiq.get("backend", "sheets")
    demo = backend == "demo"
    repo: Repository
    if demo:
        repo = InMemoryRepository()
        load_demo(repo)
    else:
        gs = _get(cfg, "gsheets")
        if not gs:
            raise StorageError("Google Sheets is not configured. Add [gsheets] to secrets, or set backend = \"demo\".")
        repo = GoogleSheetsRepository.from_config(gs, ttl=float(gs.get("cache_ttl_seconds", 30)))
    ctx = ServiceContext(repo)

    require_code = bool(auth_cfg.get("require_code", False))
    dev = require_code and (bool(auth_cfg.get("dev_show_code", False)) or demo and not _get(cfg, "smtp"))
    if not require_code:
        sender = DevScreenSender()  # unused: no codes are issued in email-only mode
    elif dev:
        sender = DevScreenSender()
    else:
        smtp = _get(cfg, "smtp")
        if not smtp:
            raise StorageError("[smtp] is not configured; cannot send sign-in codes.")
        sender = SmtpSender(_smtp_mailer(smtp))
    secret = str(auth_cfg.get("secret", "")) or ("demo-only-secret-not-for-production" if demo else "")
    auth = AuthService(ctx, sender, secret, ttl_seconds=int(auth_cfg.get("code_ttl_seconds", 600)),
                       bootstrap_admin_email=str(auth_cfg.get("bootstrap_admin_email", "")))
    smtp_cfg = _get(cfg, "smtp")
    mailer: Mailer | None = _smtp_mailer(smtp_cfg) if smtp_cfg else (OutboxMailer() if demo else None)
    return Runtime(ctx, auth, demo, dev, require_code, float(auth_cfg.get("session_hours", 8)), mailer)
