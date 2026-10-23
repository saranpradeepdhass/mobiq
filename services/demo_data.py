"""Clearly marked SAMPLE data, used by demo mode, the Sheets seeding script and the CSV template.
All names, emails and numbers are fictional. Replace or delete before real use."""
from __future__ import annotations

from datetime import date, datetime, timezone

from core.models import TABLES, fmt_bool
from services.repository import Repository

SAMPLE = "SAMPLE"
ADMIN_EMAIL = "admin@example.com"
USER_EMAIL = "alex@example.com"


def _months(today: date, n: int) -> list[str]:
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def sample_rows(today: date | None = None) -> dict[str, list[dict]]:
    today = today or datetime.now(timezone.utc).date()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    m3, m2, m1, m0 = _months(today, 4)
    due = lambda ym, d=20: f"{ym}-{d:02d}"
    users = [
        ("u_admin", f"{SAMPLE} Admin", ADMIN_EMAIL, "admin"),
        ("u_alex", f"{SAMPLE} Alex Rivera", USER_EMAIL, "user"),
        ("u_sam", f"{SAMPLE} Sam Chen", "sam@example.com", "user"),
        ("u_jo", f"{SAMPLE} Jo Patel", "jo@example.com", "user"),
    ]
    lines = [
        ("l_admin", "u_admin", "Admin phone", "5550100001", "ACCT-SAMPLE-1"),
        ("l_alex1", "u_alex", "Alex personal", "5550100002", "ACCT-SAMPLE-1"),
        ("l_alex2", "u_alex", "Alex work", "5550100003", "ACCT-SAMPLE-1"),
        ("l_sam", "u_sam", "Sam phone", "5550100004", "ACCT-SAMPLE-1"),
        ("l_jo", "u_jo", "Jo phone", "5550100005", "ACCT-SAMPLE-1"),
    ]
    owner = {l[0]: l[1] for l in lines}
    # (bill_id, month, total, status)
    bills = [("b_m3", m3, "200.00", "closed"), ("b_m2", m2, "200.00", "closed"),
             ("b_m1", m1, "210.00", "published"), ("b_m0", m0, "215.00", "published")]
    amounts = {  # per-line share amounts; each bill sums to its total
        "b_m3": {"l_admin": "40.00", "l_alex1": "40.00", "l_alex2": "40.00", "l_sam": "40.00", "l_jo": "40.00"},
        "b_m2": {"l_admin": "40.00", "l_alex1": "40.00", "l_alex2": "40.00", "l_sam": "40.00", "l_jo": "40.00"},
        "b_m1": {"l_admin": "42.00", "l_alex1": "42.00", "l_alex2": "42.00", "l_sam": "42.00", "l_jo": "42.00"},
        "b_m0": {"l_admin": "43.00", "l_alex1": "43.00", "l_alex2": "43.00", "l_sam": "43.00", "l_jo": "43.00"},
    }
    payments = [  # (share bill, line, amount, status)
        ("b_m3", "l_alex1", "40.00"), ("b_m3", "l_alex2", "40.00"), ("b_m3", "l_sam", "40.00"), ("b_m3", "l_jo", "40.00"),
        ("b_m3", "l_admin", "40.00"), ("b_m2", "l_admin", "40.00"), ("b_m2", "l_alex1", "40.00"),
        ("b_m2", "l_sam", "40.00"), ("b_m2", "l_jo", "20.00"),            # Jo: arrears from two months ago
        ("b_m1", "l_admin", "42.00"), ("b_m1", "l_alex1", "42.00"), ("b_m1", "l_alex2", "20.00"),
        ("b_m1", "l_sam", "42.00"), ("b_m0", "l_admin", "43.00"), ("b_m0", "l_alex1", "43.00"),
        ("b_m0", "l_sam", "20.00"),
    ]
    rows: dict[str, list[dict]] = {t: [] for t in TABLES}
    for uid, name, email, role in users:
        rows["Users"].append({"user_id": uid, "name": name, "email": email, "role": role,
                              "active": fmt_bool(True), "created_at": ts})
    for lid, uid, label, num, ref in lines:
        rows["MobileLines"].append({"line_id": lid, "user_id": uid, "line_label": label, "mobile_number": num,
                                    "account_reference": ref, "active": "TRUE", "created_at": ts, "updated_at": ts})
    for bid, ym, total, status in bills:
        rows["Bills"].append({"bill_id": bid, "billing_month": ym, "total_bill": total,
                              "bill_due_date": due(ym), "bill_status": status, "created_at": ts, "updated_at": ts,
                              "line_ids": ",".join(amounts[bid])})
        for lid, amt in amounts[bid].items():
            rows["BillShares"].append({"share_id": f"s_{bid}_{lid}", "bill_id": bid, "line_id": lid,
                                       "user_id": owner[lid], "amount_due": amt, "amount_paid": "", "payment_status": "",
                                       "updated_at": ts})
    for i, (bid, lid, amt) in enumerate(payments):
        ym = next(b[1] for b in bills if b[0] == bid)
        pd = f"{ym}-{min(20, 10 + i % 10):02d}"
        rows["Payments"].append({"payment_id": f"p_{i:03d}", "share_id": f"s_{bid}_{lid}", "user_id": owner[lid],
                                 "amount": amt, "payment_date": pd, "payment_status": "confirmed",
                                 "payment_method": "SAMPLE transfer", "notes": "SAMPLE data", "created_at": ts,
                                 "reverses_payment_id": ""})
    for k, v in [("app_name", "Mobiq"), ("currency", "USD"), ("timezone", "America/New_York"),
                 ("current_billing_period", m0), ("default_due_day", "20"), ("guest_visibility", "none"),
                 ("payment_instructions", "SAMPLE: Send your share via Zelle to billing@example.com"),
                 ("reminders_enabled", "FALSE")]:
        rows["Settings"].append({"key": k, "value": v, "updated_at": ts})
    return rows


def load_demo(repo: Repository, today: date | None = None) -> None:
    for table, rows in sample_rows(today).items():
        for row in rows:
            repo.append(table, row)
