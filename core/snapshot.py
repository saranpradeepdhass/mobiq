"""Load and validate all worksheets into a Snapshot. Malformed rows are skipped with a warning."""
from __future__ import annotations

from core.models import (
    AppSettings, Bill, MobileLine, Share, Snapshot, User,
)
from utils.validators import ValidationError


def _parse(rows, factory, label, warnings, id_col):
    out, seen = [], set()
    for n, row in enumerate(rows, start=2):
        try:
            item = factory(row)
        except (ValidationError, ValueError) as exc:
            warnings.append(f"{label} row {n}: skipped ({exc})")
            continue
        key = row.get(id_col)
        if key in seen:
            warnings.append(f"{label} row {n}: duplicate id {key!r} skipped")
            continue
        seen.add(key)
        out.append(item)
    return out


def load_snapshot(repo, *, fresh: bool = False) -> Snapshot:
    w: list[str] = []
    snap = Snapshot()
    snap.users = _parse(repo.list("Users", fresh=fresh), User.from_row, "Users", w, "user_id")
    snap.lines = _parse(repo.list("MobileLines", fresh=fresh), MobileLine.from_row, "MobileLines", w, "line_id")
    snap.bills = _parse(repo.list("Bills", fresh=fresh), Bill.from_row, "Bills", w, "bill_id")
    snap.shares = _parse(repo.list("BillShares", fresh=fresh), Share.from_row, "BillShares", w, "share_id")
    snap.settings, sw = AppSettings.from_rows(repo.list("Settings", fresh=fresh))
    w.extend(sw)

    bill_ids = {b.bill_id for b in snap.bills}
    line_ids = {x.line_id for x in snap.lines}
    user_ids = {u.user_id for u in snap.users}
    pairs = set()
    valid_shares = []
    for s in snap.shares:
        if s.bill_id not in bill_ids or s.line_id not in line_ids or s.user_id not in user_ids:
            w.append(f"Share {s.share_id}: references a missing bill, line or user; ignored")
        elif (s.bill_id, s.line_id) in pairs:
            w.append(f"Share {s.share_id}: duplicate of an earlier share for the same bill and line; ignored")
        else:
            pairs.add((s.bill_id, s.line_id))
            valid_shares.append(s)
    snap.shares = valid_shares
    snap.warnings = w
    return snap
