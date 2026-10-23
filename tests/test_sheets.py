"""GoogleSheetsRepository against a fake gspread spreadsheet (no network, no credentials).
This verifies our logic only; it does not prove behaviour against the real Google API."""
import gspread
import pytest

from core.models import TABLES
from services.google_sheets_service import GoogleSheetsRepository
from services.repository import ConflictError, DuplicateError, NotFoundError, SchemaError, StorageError


class FakeResponse:
    def __init__(self, code):
        self.status_code = code
        self.text = "err"
        self._code = code

    def json(self):
        return {"error": {"code": self._code, "message": "x", "status": "X"}}


class FakeWorksheet:
    def __init__(self, headers):
        self.values = [list(headers)]
        self.reads = 0
        self.fail_times = 0
        self.fail_code = 503

    def _maybe_fail(self):
        if self.fail_times:
            self.fail_times -= 1
            raise gspread.exceptions.APIError(FakeResponse(self.fail_code))

    def get_all_values(self):
        self._maybe_fail()
        self.reads += 1
        return [list(r) for r in self.values]

    def append_row(self, values, value_input_option=None, table_range=None):
        assert value_input_option == "RAW"
        self.values.append(list(values))

    def col_values(self, n):
        return [r[n - 1] if len(r) >= n else "" for r in self.values]

    def update(self, range_name, values, value_input_option=None):
        assert value_input_option == "RAW"
        row = int(range_name.split(":")[0][1:])
        self.values[row - 1] = list(values[0])


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = {t: FakeWorksheet(h) for t, (_, h) in TABLES.items()}

    def worksheet(self, name):
        return self.sheets[name]


@pytest.fixture
def ss():
    return FakeSpreadsheet()


@pytest.fixture
def repo(ss):
    now = {"t": 0.0}
    r = GoogleSheetsRepository(ss, ttl=30, sleep=lambda s: None, clock=lambda: now["t"])
    r.now = now
    return r


def user(i, ts=""):
    return {"user_id": i, "name": i, "email": f"{i}@example.com", "role": "user", "active": "TRUE", "created_at": ts}


def test_append_and_list_preserve_text(repo):
    repo.append("Users", user("u1"))
    repo.append("MobileLines", {"line_id": "l1", "user_id": "u1", "line_label": "A", "mobile_number": "0550100002",
                                "active": "TRUE"})
    assert repo.list("Users")[0]["email"] == "u1@example.com"
    assert repo.list("MobileLines")[0]["mobile_number"] == "0550100002"  # leading zero kept


def test_reads_are_cached_and_invalidated_on_write(repo, ss):
    repo.list("Users")
    repo.list("Users")
    assert ss.sheets["Users"].reads == 1
    repo.append("Users", user("u1"))
    repo.list("Users")
    assert ss.sheets["Users"].reads >= 2 and len(repo.list("Users")) == 1


def test_cache_expires_after_ttl(repo, ss):
    repo.list("Users")
    repo.now["t"] = 31
    repo.list("Users")
    assert ss.sheets["Users"].reads == 2


def test_duplicate_id_rejected(repo):
    repo.append("Users", user("u1"))
    with pytest.raises(DuplicateError):
        repo.append("Users", user("u1"))


def test_update_and_optimistic_concurrency(repo):
    repo.append("Bills", {"bill_id": "b1", "billing_month": "2026-10", "total_bill": "1.00", "bill_due_date": "2026-10-25",
                          "bill_status": "draft", "updated_at": "v1"})
    row = repo.update("Bills", "b1", {"total_bill": "2.00", "updated_at": "v2"}, expected_updated_at="v1")
    assert row["total_bill"] == "2.00" and repo.list("Bills", fresh=True)[0]["updated_at"] == "v2"
    with pytest.raises(ConflictError):
        repo.update("Bills", "b1", {"total_bill": "3.00"}, expected_updated_at="v1")
    with pytest.raises(NotFoundError):
        repo.update("Bills", "nope", {})


def test_update_targets_correct_sheet_row_with_blank_rows(repo, ss):
    repo.append("Users", user("u1"))
    ss.sheets["Users"].values.insert(2, [""] * 6)  # a blank row someone left in the sheet
    repo.append("Users", user("u2"))
    repo.update("Users", "u2", {"name": "Changed"})
    assert [r[1] for r in ss.sheets["Users"].values[1:]] == ["u1", "", "Changed"]


def test_transient_errors_retried_then_succeed(repo, ss):
    ss.sheets["Users"].fail_times = 2
    assert repo.list("Users") == []


def test_persistent_api_failure_becomes_storage_error_without_leaking_details(repo, ss):
    ss.sheets["Users"].fail_times = 99
    with pytest.raises(StorageError) as e:
        repo.list("Users")
    assert "503" in str(e.value)
    ss.sheets["Users"].fail_times, ss.sheets["Users"].fail_code = 1, 403
    repo.invalidate()
    with pytest.raises(StorageError):
        repo.list("Users")  # 403 is not retried


def test_missing_columns_raise_schema_error(ss):
    ss.sheets["Users"].values = [["user_id", "name"]]
    r = GoogleSheetsRepository(ss)
    with pytest.raises(SchemaError, match="email"):
        r.list("Users")


# DISABLED: the Payments worksheet no longer exists.
# def test_blank_rows_ignored_and_payments_legacy_header_allowed(ss):
#     ss.sheets["Payments"].values = [[h for h in TABLES["Payments"][1] if h != "reverses_payment_id"], [""] * 9]
#     r = GoogleSheetsRepository(ss)
#     assert r.list("Payments") == []


