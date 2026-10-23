"""Email sign-in with self-registration and an admin PIN.

Modes (auth.require_code):
- false (default): email-only. Existing users sign in with their email. NO proof of mailbox ownership.
- true: an emailed 6-digit one-time code is required.

Everyone: unknown email -> the app asks for a mobile number and creates a user account plus a line owned by
that user (auth.allow_registration). Admin accounts additionally need the 4-digit PIN from auth.admin_pin;
the PIN is enforced here, not just in the UI, and is locked after repeated wrong attempts.

Codes are random, single-use, expire, are stored only as HMAC hashes and are never logged. Attempts and
registrations are rate limited. State lives in process memory (see README for hosting limitations).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from core.models import MobileLine, User, fmt_bool, new_id
from core.snapshot import load_snapshot
from services.common import AuthenticationError, Principal, ServiceContext, audit
from services.mailer import DeliveryError, Mailer  # noqa: F401  (DeliveryError re-exported)
from services.repository import DuplicateError
from utils.validators import ValidationError, normalize_email, normalize_phone

GENERIC_SENT = "If that email is registered, a sign-in code has been sent."
GENERIC_FAIL = "That code is invalid or has expired."
NO_ACCOUNT = "We couldn't sign you in with that email. Ask an administrator to invite you."
INACTIVE = "This account is disabled. Ask an administrator for help."
PIN_NOT_CONFIGURED = "Admin sign-in is disabled because no admin PIN is configured. Set auth.admin_pin in the secrets."
PIN_WRONG = "Incorrect PIN."

_WEAK_PINS = {"0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
              "1234", "4321", "0123", "3210", "1212", "2580", "1004", "2000"}


def validate_admin_pin(pin: str | None) -> str | None:
    """Return the PIN if it is exactly four digits and not trivially guessable; raise ValueError otherwise."""
    if pin is None or pin == "":
        return None
    if not re.fullmatch(r"\d{4}", pin):
        raise ValueError("auth.admin_pin must be exactly 4 digits (quote it in TOML, e.g. admin_pin = \"4821\").")
    if pin in _WEAK_PINS:
        raise ValueError("auth.admin_pin is too easy to guess (avoid repeated or sequential digits).")
    return pin


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


def _display_name(email: str) -> str:
    local = re.sub(r"[._\-+]+", " ", email.split("@", 1)[0]).strip()
    return (local.title() or "New user")[:80]


class AuthService:
    def __init__(self, ctx: ServiceContext, sender: EmailSender, secret: str, *,
                 ttl_seconds: int = 600, max_attempts: int = 5, max_sends: int = 3,
                 send_window: int = 900, cooldown: int = 30, global_per_minute: int = 30,
                 bootstrap_admin_email: str = "", admin_pin: str | None = None,
                 allow_registration: bool = True, max_registrations_per_hour: int = 10,
                 pin_max_failures: int = 5, pin_lock_seconds: int = 900,
                 monotonic: Callable[[], float] = time.monotonic):
        if len(secret) < 16:
            raise ValueError("auth secret must be at least 16 characters")
        self.ctx, self.sender, self._secret = ctx, sender, secret.encode()
        self.ttl, self.max_attempts, self.max_sends = ttl_seconds, max_attempts, max_sends
        self.window, self.cooldown, self.global_per_minute = send_window, cooldown, global_per_minute
        self.bootstrap = bootstrap_admin_email.strip().lower()
        self._admin_pin = validate_admin_pin(admin_pin)
        self.allow_registration = allow_registration
        self.max_registrations_per_hour = max_registrations_per_hour
        self.pin_max_failures, self.pin_lock_seconds = pin_max_failures, pin_lock_seconds
        self._mono = monotonic
        self._pending: dict[str, _Pending] = {}
        self._pending_reg: dict[str, str] = {}   # email key -> mobile number awaiting a verified code
        self._sends: dict[str, list[float]] = {}     # code e-mails sent
        self._attempts: dict[str, list[float]] = {}  # lookups / sign-in attempts
        self._pin_failures: dict[str, list[float]] = {}
        self._registrations: list[float] = []
        self._global: list[float] = []
        self._lock = threading.Lock()

    # ---- helpers ---------------------------------------------------------
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

    def _throttle(self, key: str, limit: int = 10) -> None:
        """Limit lookups / sign-in attempts per email (and globally) to slow enumeration and guessing."""
        now = self._mono()
        with self._lock:
            self._global = [t for t in self._global if now - t < 60]
            tries = [t for t in self._attempts.get(key, []) if now - t < self.window]
            if len(self._global) >= self.global_per_minute or len(tries) >= limit:
                raise RateLimited("Too many sign-in attempts. Try again shortly.")
            tries.append(now)
            self._attempts[key] = tries
            self._global.append(now)

    def _check_send_limits(self, key: str) -> float:
        now = self._mono()
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
        return now

    def _issue_code(self, email: str, key: str, now: float) -> str | None:
        code = f"{secrets.randbelow(10**6):06d}"
        shown = self.sender.send_code(email, code, self.ttl // 60, "Mobiq")
        with self._lock:
            self._pending[key] = _Pending(self._digest(email, code), now + self.ttl)
        return shown

    # ---- accounts --------------------------------------------------------
    def account_status(self, email_input: str) -> str:
        """'new' | 'member' | 'admin' | 'inactive'. Drives the login screen (mobile number / PIN prompts).
        Open registration makes 'is this email known?' answerable by design; lookups are rate limited."""
        email = normalize_email(email_input)
        self._throttle("status:" + self._key(email))
        self._maybe_bootstrap(email)
        user = self._find_user(email)
        if user is None:
            return "new"
        if not user.active:
            return "inactive"
        return "admin" if user.role == "admin" else "member"

    def _create_user(self, email: str, mobile: str) -> User:
        """Create a user and one line for their mobile number. Refuses duplicates (email or active number)."""
        with self.ctx.repo.lock():
            snap = load_snapshot(self.ctx.repo, fresh=True)
            if any(u.email == email for u in snap.users):
                raise DuplicateError("That email is already registered. Sign in instead.")
            if any(x.active and x.mobile_number == mobile for x in snap.lines):
                raise DuplicateError("That mobile number is already registered to another account.")
            now = self.ctx.stamp()
            user = User(new_id("usr"), _display_name(email), email, "user", True, now)
            line = MobileLine(new_id("line"), user.user_id, "My phone", mobile, "", True, now, now)
            self.ctx.repo.append("Users", user.to_row())
            self.ctx.repo.append("MobileLines", line.to_row())
            audit(self.ctx, Principal(user.user_id, user.email, user.name, user.role), "user.register",
                  "user", user.user_id, f"last4={mobile[-4:]}")
        return user

    def _registration_allowed(self) -> None:
        if not self.allow_registration:
            raise AuthenticationError(NO_ACCOUNT)

    def register(self, email_input: str, mobile_input: str) -> Principal:
        """Email-only mode: create the account for a new email, mapped to the given mobile number."""
        self._registration_allowed()
        email = normalize_email(email_input)
        mobile = normalize_phone(mobile_input)
        self._throttle("status:" + self._key(email))
        now = self._mono()
        with self._lock:
            self._registrations = [t for t in self._registrations if now - t < 3600]
            if len(self._registrations) >= self.max_registrations_per_hour:
                raise RateLimited("Too many new accounts were created recently. Try again later.")
            self._registrations.append(now)
        user = self._create_user(email, mobile)
        return Principal(user.user_id, user.email, user.name, user.role)

    # ---- admin PIN -------------------------------------------------------
    def check_admin_pin(self, email_input: str, pin_input: str | None) -> None:
        """Raise unless pin_input matches the configured PIN. Locks the email after repeated failures."""
        email = normalize_email(email_input)
        if self._admin_pin is None:
            raise AuthenticationError(PIN_NOT_CONFIGURED)
        key, now = self._key(email), self._mono()
        with self._lock:
            failures = [t for t in self._pin_failures.get(key, []) if now - t < self.pin_lock_seconds]
            self._pin_failures[key] = failures
            if len(failures) >= self.pin_max_failures:
                raise RateLimited("Too many incorrect PINs. Try again in 15 minutes.")
        supplied = str(pin_input or "").strip()
        if not hmac.compare_digest(supplied.encode(), self._admin_pin.encode()):
            with self._lock:
                self._pin_failures.setdefault(key, []).append(now)
            raise AuthenticationError(PIN_WRONG)
        with self._lock:
            self._pin_failures.pop(key, None)

    # ---- sign-in ---------------------------------------------------------
    def request_code(self, email_input: str) -> tuple[str, str | None]:
        """Returns (generic message, dev-only code to display). Raises RateLimited / DeliveryError."""
        email = normalize_email(email_input)
        key = self._key(email)
        now = self._check_send_limits(key)
        self._maybe_bootstrap(email)
        user = self._find_user(email)
        if user is None or not user.active:
            return GENERIC_SENT, None  # no email sent, same response and timing class as success
        return GENERIC_SENT, self._issue_code(email, key, now)

    def request_registration_code(self, email_input: str, mobile_input: str) -> tuple[str, str | None]:
        """Code mode: a new email proves it owns the mailbox before the account is created at verify_code()."""
        self._registration_allowed()
        email = normalize_email(email_input)
        mobile = normalize_phone(mobile_input)
        key = self._key(email)
        self._throttle("status:" + key)  # caps lookups; a rejected request must not use up the one-code-per-30s allowance
        snap = load_snapshot(self.ctx.repo, fresh=True)
        if any(u.email == email for u in snap.users):
            raise DuplicateError("That email is already registered. Sign in instead.")
        if any(x.active and x.mobile_number == mobile for x in snap.lines):
            raise DuplicateError("That mobile number is already registered to another account.")
        now = self._check_send_limits(key)
        shown = self._issue_code(email, key, now)
        with self._lock:
            self._pending_reg[key] = mobile
        return GENERIC_SENT, shown

    def sign_in_email_only(self, email_input: str, *, admin_pin: str | None = None) -> Principal:
        """Sign in with just an email address (NO verification of mailbox ownership). Admin accounts also need
        the admin PIN. Anyone who knows an ordinary user's email gets that user's access. Rate limited."""
        email = normalize_email(email_input)  # raises ValidationError for empty/invalid input
        self._throttle("status:" + self._key(email))
        self._maybe_bootstrap(email)
        user = self._find_user(email)
        if user is None:
            raise AuthenticationError(NO_ACCOUNT)
        if not user.active:
            raise AuthenticationError(INACTIVE)
        if user.role == "admin":
            self.check_admin_pin(email, admin_pin)
        return Principal(user.user_id, user.email, user.name, user.role)

    def verify_code(self, email_input: str, code_input: str, *, admin_pin: str | None = None) -> Principal:
        try:
            email = normalize_email(email_input)
        except ValidationError:
            raise AuthenticationError(GENERIC_FAIL) from None
        existing = self._find_user(email)
        if existing is not None and existing.active and existing.role == "admin":
            self.check_admin_pin(email, admin_pin)  # before the code is consumed, so a typo does not burn it
        code = "".join(ch for ch in str(code_input) if ch.isdigit())
        key, now = self._key(email), self._mono()
        with self._lock:
            pending = self._pending.get(key)
            if pending is None or now > pending.expires:
                self._pending.pop(key, None)
                self._pending_reg.pop(key, None)
                raise AuthenticationError(GENERIC_FAIL)
            pending.attempts += 1
            if pending.attempts > self.max_attempts:
                self._pending.pop(key, None)
                self._pending_reg.pop(key, None)
                raise AuthenticationError(GENERIC_FAIL)
            if len(code) != 6 or not hmac.compare_digest(pending.digest, self._digest(email, code)):
                raise AuthenticationError(GENERIC_FAIL)
            del self._pending[key]  # single use
            registering_mobile = self._pending_reg.pop(key, None)
        user = self._find_user(email)
        if user is None and registering_mobile and self.allow_registration:
            user = self._create_user(email, registering_mobile)
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
