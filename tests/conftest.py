from datetime import datetime, timedelta, timezone

import pytest

from core.models import fmt_bool
from services.common import Principal, ServiceContext
from services.repository import InMemoryRepository

NOW = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)  # period 2026-10, today 2026-10-22 (New York)


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def ctx(repo):
    ticks = {"n": 0}

    def clock():  # advances 1s per call so updated_at values are distinct (optimistic-concurrency tests)
        ticks["n"] += 1
        return NOW + timedelta(seconds=ticks["n"])

    return ServiceContext(repo, clock=clock)


def add_user(repo, uid, email, role="user", active=True, name=None):
    repo.append("Users", {"user_id": uid, "name": name or uid.title(), "email": email, "role": role,
                          "active": fmt_bool(active), "created_at": "t"})


def add_line(repo, lid, uid, label=None, number="5550100001", active=True):
    repo.append("MobileLines", {"line_id": lid, "user_id": uid, "line_label": label or lid, "mobile_number": number,
                                "account_reference": "", "active": fmt_bool(active), "created_at": "t", "updated_at": "t0"})


@pytest.fixture
def world(repo, ctx):
    """admin + two users; alex owns two lines, sam one. Settings: period 2026-10."""
    add_user(repo, "u_admin", "admin@example.com", "admin")
    add_user(repo, "u_alex", "alex@example.com")
    add_user(repo, "u_sam", "sam@example.com")
    add_line(repo, "l_alex1", "u_alex", "Alex personal", "5550100002")
    add_line(repo, "l_alex2", "u_alex", "Alex work", "5550100003")
    add_line(repo, "l_sam", "u_sam", "Sam phone", "5550100004")
    repo.append("Settings", {"key": "current_billing_period", "value": "2026-10", "updated_at": "t"})
    return repo


@pytest.fixture
def admin():
    return Principal("u_admin", "admin@example.com", "Admin", "admin")


@pytest.fixture
def alex():
    return Principal("u_alex", "alex@example.com", "Alex", "user")


def add_payment(repo, share_id, amount, status="confirmed", pid=None, date="2026-10-05"):
    """Insert a payment row directly (partial/pending payments can exist in the sheet; the UI only toggles paid)."""
    import itertools
    n = len(repo.list("Payments"))
    repo.append("Payments", {"payment_id": pid or f"p_t{n}", "share_id": share_id, "user_id": "u_x", "amount": amount,
                             "payment_date": date, "payment_status": status, "payment_method": "", "notes": "",
                             "created_at": "t", "reverses_payment_id": ""})
