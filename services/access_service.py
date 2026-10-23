"""Read-side authorization: which records a principal may see. Pure functions, no I/O.

- admin: every non-draft share (drafts are managed in the Admin Console)
- user: only shares on lines owned by that user
- guest: nothing by default; per the guest_visibility setting, anonymised aggregates or statuses only
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.models import Snapshot
from services.common import Principal
from utils.calculations import ShareView, Status, ZERO, current_views, q


def visible_views(principal: Principal, views: list[ShareView]) -> list[ShareView]:
    if principal.is_guest or principal.user_id is None:
        return []
    if principal.is_admin:
        return list(views)
    return [v for v in views if v.share.user_id == principal.user_id]


def own_lines(snap: Snapshot, principal: Principal, views: list[ShareView]):
    """The principal's active lines, plus inactive lines that still carry history, for line cards."""
    if principal.is_guest or not principal.user_id:
        return []
    with_history = {v.share.line_id for v in views if v.share.user_id == principal.user_id}
    return [x for x in snap.lines if x.user_id == principal.user_id and (x.active or x.line_id in with_history)]


@dataclass(frozen=True)
class GuestView:
    policy: str
    period: str
    charges: Decimal | None = None
    collected: Decimal | None = None
    paid_count: int | None = None
    total_count: int | None = None
    statuses: tuple[tuple[str, str], ...] = ()  # ("Line 1", "Paid")


def guest_view(policy: str, all_views: list[ShareView], period: str) -> GuestView | None:
    if policy == "aggregate":
        cur = current_views(all_views, period)
        return GuestView(policy, period,
                         charges=q(sum((v.amount_due for v in cur), ZERO)),
                         collected=q(sum((min(v.paid, v.amount_due) for v in cur), ZERO)),
                         paid_count=sum(1 for v in cur if v.status is Status.PAID), total_count=len(cur))
    if policy == "status_only":
        cur = current_views(all_views, period)
        return GuestView(policy, period, statuses=tuple((f"Line {i}", v.status.value) for i, v in enumerate(cur, 1)))
    return None
