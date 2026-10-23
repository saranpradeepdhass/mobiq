"""Email sign-in: either emailed one-time codes (auth.require_code = true) or email-only (default).

Flow: request_code(email) -> emailed 6-digit code -> verify_code(email, code) -> Principal.
- Only emails that already exist in the Users sheet as active are sent a code (invitation model);
  the response is identical for unknown emails so accounts cannot be enumerated.
- Codes are random, single-use, expire (default 10 min), stored only as HMAC hashes, and are
  invalidated after too many wrong attempts. Sends are rate limited per email and globally.
- Codes are never logged. State lives in process memory (see README for hosting limitations).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from core.models import User, fmt_bool, new_id
from services.common import AuthenticationError, Principal, ServiceContext
from services.mailer import DeliveryError, Mailer  # noqa: F401  (DeliveryError re-exported)
from utils.validators import ValidationError, normalize_email

GENERIC_SENT = "If that email is registered, a sign-in code has been sent."
GENERIC_FAIL = "That code is invalid or has expired."
NO_ACCOUNT = "We couldn't sign you in with that email. Ask an administrator to invite you."


class RateLimited(Exception):
    pass


class EmailSender(Protocol):
    def send_code(self, email: str, code: str, ttl_minutes: int, app_name: str) -> str | None:
        """Deliver the code. May return a string to display in the UI (development sender only)."""


class SmtpSender:
    """Sends sign-in codes through a Mailer."""

    def __init__(self, mailer: Mailer):
        self._mailer = mailer

    def send_code(self, email, code, ttl_minutes, app_name):
        self._mailer.send_message(
            email, f"Your {app_name} sign-in code",
            f"Your {app_name} sign-in code is {code}. It expires in {ttl_minutes} minutes.\n"
            "If you did not request it, you can ignore this email.")
        return None


class DevScreenSender:
    """DEVELOPMENT ONLY: returns the code so the UI can show it. Never enable on a public deployment."""

    def send_code(self, email, code, ttl_minutes, app_name):
        return code


@dataclass
class _Pending:
    digest: str
    expires: float
    attempts: int = 0


class AuthService:
    def __init__(self, ctx: ServiceContext, sender: EmailSender, secret: str, *,
                 ttl_seconds: int = 600, max_attempts: int = 5, max_sends: int = 3,
                 send_window: int = 900, cooldown: int = 30, global_per_minute: int = 30,
                 bootstrap_admin_email: str = "", monotonic: Callable[[], float] = time.monotonic):
        if len(secret) < 16:
            raise ValueError("auth secret must be at least 16 characters")
        self.ctx, self.sender, self._secret = ctx, sender, secret.encode()
        self.ttl, self.max_attempts, self.max_sends = ttl_seconds, max_attempts, max_sends
        self.window, self.cooldown, self.global_per_minute = send_window, cooldown, global_per_minute
        self.bootstrap = bootstrap_admin_email.strip().lower()
        self._mono = monotonic
        self._pending: dict[str, _Pending] = {}
        self._sends: dict[str, list[float]] = {}
        self._global: list[float] = []
        self._lock = threading.Lock()

    # -- helpers
    def _key(self, email: str) -> str:
        return hmac.new(self._secret, b"k:" + email.encode(), hashlib.sha256).hexdigest()

    def _digest(self, email: str, code: str) -> str:
        return hmac.new(self._secret, f"c:{email}:{code}".encode(), hashlib.sha256).hexdigest()

    def _find_user(self, email: str) -> User | None:
        for row in self.ctx.repo.list("Users", fresh=True):
            try:
                u = User.from_row(row)
            except (ValidationError, ValueError):
                continue
            if u.email == email:
                return u
        return None

    def _maybe_bootstrap(self, email: str) -> None:
        """Create the very first admin from configuration, only while no admin exists."""
        if not self.bootstrap or email != self.bootstrap:
            return
        rows = self.ctx.repo.list("Users", fresh=True)
        if any(r.get("role", "").lower() == "admin" for r in rows):
            return
        self.ctx.repo.append("Users", {
            "user_id": new_id("usr"), "name": "Administrator", "email": email, "role": "admin",
            "active": fmt_bool(True), "created_at": self.ctx.stamp()})

    # -- API
    def request_code(self, email_input: str) -> tuple[str, str | None]:
        """Returns (generic message, dev-only code to display). Raises RateLimited / DeliveryError."""
        email = normalize_email(email_input)
        now = self._mono()
        key = self._key(email)
        with self._lock:
            self._global = [t for t in self._global if now - t < 60]
            sends = [t for t in self._sends.get(key, []) if now - t < self.window]
            if len(self._global) >= self.global_per_minute:
                raise RateLimited("Too many sign-in requests right now. Try again shortly.")
            if sends and now - sends[-1] < self.cooldown:
                raise RateLimited("Please wait a moment before requesting another code.")
            if len(sends) >= self.max_sends:
                raise RateLimited("Too many codes requested. Try again later.")
            sends.append(now)
            self._sends[key] = sends
            self._global.append(now)

        self._maybe_bootstrap(email)
        user = self._find_user(email)
        if user is None or not user.active:
            return GENERIC_SENT, None  # no email sent, same response and timing class as success

        code = f"{secrets.randbelow(10**6):06d}"
        shown = self.sender.send_code(email, code, self.ttl // 60, "Mobiq")
        with self._lock:
            self._pending[key] = _Pending(self._digest(email, code), now + self.ttl)
        return GENERIC_SENT, shown

    def sign_in_email_only(self, email_input: str) -> Principal:
        """Sign in with just an email address (NO verification of mailbox ownership).
        Anyone who knows or guesses an active user's email gets that user's access, including admin.
        Enabled only when auth.require_code is false. Attempts are rate limited to slow enumeration."""
        email = normalize_email(email_input)  # raises ValidationError for empty/invalid input
        now, key = self._mono(), self._key(email)
        with self._lock:
            self._global = [t for t in self._global if now - t < 60]
            tries = [t for t in self._sends.get(key, []) if now - t < self.window]
            if len(self._global) >= self.global_per_minute or len(tries) >= 10:
                raise RateLimited("Too many sign-in attempts. Try again shortly.")
            tries.append(now)
            self._sends[key] = tries
            self._global.append(now)
        self._maybe_bootstrap(email)
        user = self._find_user(email)
        if user is None or not user.active:
            raise AuthenticationError(NO_ACCOUNT)
        return Principal(user.user_id, user.email, user.name, user.role)

    def verify_code(self, email_input: str, code_input: str) -> Principal:
        try:
            email = normalize_email(email_input)
        except ValidationError:
            raise AuthenticationError(GENERIC_FAIL) from None
        code = "".join(ch for ch in str(code_input) if ch.isdigit())
        key, now = self._key(email), self._mono()
        with self._lock:
            pending = self._pending.get(key)
            if pending is None or now > pending.expires:
                self._pending.pop(key, None)
                raise AuthenticationError(GENERIC_FAIL)
            pending.attempts += 1
            if pending.attempts > self.max_attempts:
                self._pending.pop(key, None)
                raise AuthenticationError(GENERIC_FAIL)
            if len(code) != 6 or not hmac.compare_digest(pending.digest, self._digest(email, code)):
                raise AuthenticationError(GENERIC_FAIL)
            del self._pending[key]  # single use
        user = self._find_user(email)
        if user is None or not user.active:
            raise AuthenticationError(GENERIC_FAIL)
        return Principal(user.user_id, user.email, user.name, user.role)


def resolve_principal(ctx: ServiceContext, user_id: str | None, *, fresh: bool = False) -> Principal | None:
    """Rebuild a principal from storage. Uses the short-lived cache by default (role/active changes show up
    within the cache TTL); sensitive writes re-check with require_admin(fresh=True)."""
    if not user_id:
        return None
    row = ctx.repo.get("Users", user_id, fresh=fresh)
    if not row:
        return None
    try:
        u = User.from_row(row)
    except (ValidationError, ValueError):
        return None
    return Principal(u.user_id, u.email, u.name, u.role) if u.active else None
