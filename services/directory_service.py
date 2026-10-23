"""Admin operations on users, mobile lines and settings. Every function enforces require_admin."""
from __future__ import annotations

from core.models import MobileLine, User, fmt_bool, new_id
from core.snapshot import load_snapshot
from services.common import (
    BusinessRuleError, Principal, ServiceContext, audit, require_admin,
)
from services.repository import DuplicateError, NotFoundError
from utils.validators import (
    GUEST_POLICIES, ROLES, ValidationError, choice, clean_text, normalize_email, normalize_phone,
    parse_bool, parse_month,
)


def _admins(snap):
    return [u for u in snap.users if u.role == "admin" and u.active]


def invite_user(ctx: ServiceContext, actor: Principal, *, name: str, email: str, role: str = "user") -> User:
    """Invitation = creating an active user. They then sign in with an emailed code."""
    require_admin(ctx, actor)
    name = clean_text(name, "Name", max_len=80)
    email = normalize_email(email)
    role = choice(role, ROLES, "Role")
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        if any(u.email == email for u in snap.users):
            raise DuplicateError("A user with that email already exists.")
        user = User(new_id("usr"), name, email, role, True, ctx.stamp())
        ctx.repo.append("Users", user.to_row())
        audit(ctx, actor, "user.invite", "user", user.user_id, f"role={role}")
    return user


def update_user(ctx: ServiceContext, actor: Principal, user_id: str, *, name: str | None = None,
                role: str | None = None, active: bool | None = None) -> None:
    require_admin(ctx, actor)
    changes = {}
    if name is not None:
        changes["name"] = clean_text(name, "Name", max_len=80)
    if role is not None:
        changes["role"] = choice(role, ROLES, "Role")
    if active is not None:
        changes["active"] = fmt_bool(parse_bool(active))
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        target = snap.user(user_id)
        if target is None:
            raise NotFoundError("User not found.")
        losing_admin = target.role == "admin" and target.active and (
            changes.get("role", "admin") != "admin" or changes.get("active", "TRUE") != "TRUE")
        if losing_admin and (len(_admins(snap)) <= 1 or user_id == actor.user_id):
            raise BusinessRuleError("You cannot remove or deactivate the last admin, or your own admin access.")
        ctx.repo.update("Users", user_id, changes)
        audit(ctx, actor, "user.update", "user", user_id, ",".join(sorted(changes)))


def add_line(ctx: ServiceContext, actor: Principal, *, user_id: str, line_label: str,
             mobile_number: str, account_reference: str = "") -> MobileLine:
    require_admin(ctx, actor)
    label = clean_text(line_label, "Line label", max_len=40)
    number = normalize_phone(mobile_number)
    ref = clean_text(account_reference, "Account reference", max_len=60, required=False)
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        owner = snap.user(user_id)
        if owner is None or not owner.active:
            raise BusinessRuleError("The owner must be an existing active user.")
        _check_unique(snap, user_id, label, number)
        now = ctx.stamp()
        line = MobileLine(new_id("line"), user_id, label, number, ref, True, now, now)
        ctx.repo.append("MobileLines", line.to_row())
        audit(ctx, actor, "line.add", "line", line.line_id, f"last4={number[-4:]}")
    return line


def _check_unique(snap, user_id, label, number, exclude: str | None = None):
    for x in snap.lines:
        if x.line_id == exclude:
            continue
        if x.active and x.mobile_number == number:
            raise DuplicateError("That mobile number is already assigned to an active line.")
        if x.user_id == user_id and x.active and x.line_label.lower() == label.lower():
            raise DuplicateError("This user already has an active line with that label.")


def update_line(ctx: ServiceContext, actor: Principal, line_id: str, *, line_label: str | None = None,
                mobile_number: str | None = None, account_reference: str | None = None,
                expected_updated_at: str | None = None) -> None:
    """Changing a number keeps line_id, so all historical shares/payments stay attached."""
    require_admin(ctx, actor)
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        line = snap.line(line_id)
        if line is None:
            raise NotFoundError("Line not found.")
        label = clean_text(line_label, "Line label", max_len=40) if line_label is not None else line.line_label
        number = normalize_phone(mobile_number) if mobile_number is not None else line.mobile_number
        ref = (clean_text(account_reference, "Account reference", max_len=60, required=False)
               if account_reference is not None else line.account_reference)
        if line.active:
            _check_unique(snap, line.user_id, label, number, exclude=line_id)
        ctx.repo.update("MobileLines", line_id,
                        {"line_label": label, "mobile_number": number, "account_reference": ref,
                         "updated_at": ctx.stamp()}, expected_updated_at=expected_updated_at)
        detail = f"last4 {line.mobile_number[-4:]}->{number[-4:]}" if number != line.mobile_number else "details"
        audit(ctx, actor, "line.update", "line", line_id, detail)


def set_line_active(ctx: ServiceContext, actor: Principal, line_id: str, active: bool) -> None:
    require_admin(ctx, actor)
    with ctx.repo.lock():
        snap = load_snapshot(ctx.repo, fresh=True)
        line = snap.line(line_id)
        if line is None:
            raise NotFoundError("Line not found.")
        if active:
            owner = snap.user(line.user_id)
            if owner is None or not owner.active:
                raise BusinessRuleError("Cannot activate a line whose owner is inactive.")
            _check_unique(snap, line.user_id, line.line_label, line.mobile_number, exclude=line_id)
        ctx.repo.update("MobileLines", line_id,
                        {"active": fmt_bool(active), "updated_at": ctx.stamp()})
        audit(ctx, actor, "line.activate" if active else "line.deactivate", "line", line_id)


def update_setting(ctx: ServiceContext, actor: Principal, key: str, value: str) -> None:
    require_admin(ctx, actor)
    allowed = {"app_name", "currency", "timezone", "current_billing_period", "default_due_day",
               "guest_visibility", "payment_instructions", "reminders_enabled"}
    if key not in allowed:
        raise ValidationError("Unknown setting.")
    value = str(value).strip()
    # Validate by parsing the would-be settings.
    from core.models import AppSettings
    _, warns = AppSettings.from_rows([{"key": key, "value": value}])
    if warns:
        raise ValidationError(warns[0])
    if key == "guest_visibility":
        choice(value, GUEST_POLICIES, "Guest visibility")
    if key == "current_billing_period" and value:
        parse_month(value)
    with ctx.repo.lock():
        row = ctx.repo.get("Settings", key, fresh=True)
        payload = {"key": key, "value": value, "updated_at": ctx.stamp()}
        if row:
            ctx.repo.update("Settings", key, payload)
        else:
            ctx.repo.append("Settings", payload)
        audit(ctx, actor, "setting.update", "setting", key)
