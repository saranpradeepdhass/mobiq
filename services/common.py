"""Shared service plumbing: context, errors, authorization guard and audit logging."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from core.models import new_id
from services.repository import Repository
from utils.formatting import iso, utc_now


class AuthenticationError(Exception):
    """Identity could not be verified. Messages are deliberately generic."""


class AuthorizationError(PermissionError):
    """Authenticated but not allowed to perform the action."""


class ConfirmationRequired(Exception):
    """A sensitive change was attempted without explicit confirmation."""


class BusinessRuleError(ValueError):
    """A request was well-formed but violates a business rule."""


@dataclass(frozen=True)
class Principal:
    user_id: str | None
    email: str | None
    name: str
    role: str  # "admin" | "user" | "guest"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_guest(self) -> bool:
        return self.role == "guest"


GUEST = Principal(user_id=None, email=None, name="Guest", role="guest")


@dataclass
class ServiceContext:
    repo: Repository
    clock: Callable[[], datetime] = field(default=utc_now)

    def now(self) -> datetime:
        return self.clock()

    def stamp(self) -> str:
        return iso(self.now())


def require_admin(ctx: ServiceContext, actor: Principal, *, fresh: bool = True) -> Principal:
    """Server-side authorization. Re-reads the user from storage; the session's role is never trusted.
    Writes always use fresh=True (a just-demoted admin is refused immediately); the read-only page guard may
    pass fresh=False to use the short-lived cache."""
    if actor is None or actor.is_guest or not actor.user_id:
        raise AuthorizationError("Administrator access is required.")
    row = ctx.repo.get("Users", actor.user_id, fresh=fresh)
    if not row or row.get("role", "").strip().lower() != "admin" \
            or row.get("active", "").strip().upper() != "TRUE":
        raise AuthorizationError("Administrator access is required.")
    return actor


def audit(ctx: ServiceContext, actor: Principal, action: str, entity_type: str,
          entity_id: str, details: str = "") -> None:
    """Append-only audit entry. Details must not contain credentials or full phone numbers."""
    ctx.repo.append("AuditLog", {
        "audit_id": new_id("aud"), "timestamp": ctx.stamp(),
        "actor_user_id": actor.user_id or "", "action": action,
        "entity_type": entity_type, "entity_id": entity_id, "details": details[:500],
    })
