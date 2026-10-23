"""Google Sheets implementation of Repository (gspread).

Notes
- Reads use get_all_values() so IDs, phone numbers and money stay as text (no type coercion).
- Writes use value_input_option="RAW" so values are never interpreted as formulas.
- Reads are cached per worksheet for `ttl` seconds and invalidated after every write.
- Updates re-read the worksheet fresh to locate the row and run the optimistic-concurrency check.
- This module has NOT been exercised against a live spreadsheet in this repository's test suite;
  tests use InMemoryRepository. Follow the README smoke-test steps with your own credentials.
"""
from __future__ import annotations

import time
from typing import Callable, Mapping

from core.models import TABLES
from services.repository import (
    ConflictError, DuplicateError, NotFoundError, Repository, SchemaError, StorageError,
)

_RETRYABLE = {429, 500, 502, 503, 504}


class GoogleSheetsRepository(Repository):
    def __init__(self, spreadsheet, *, ttl: float = 30.0, retries: int = 3,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._ss = spreadsheet
        self._ttl = ttl
        self._retries = retries
        self._sleep = sleep
        self._clock = clock
        self._cache: dict[str, tuple[float, list[str], list[list[str]]]] = {}
        self._ws: dict = {}

    # ---- construction -------------------------------------------------
    @classmethod
    def from_config(cls, cfg: Mapping, **kwargs) -> "GoogleSheetsRepository":
        """cfg: {'spreadsheet_id': ..., 'service_account': {...service account JSON fields...}}"""
        import gspread  # imported lazily so tests and demo mode do not need credentials

        try:
            client = gspread.service_account_from_dict(dict(cfg["service_account"]))
            return cls(client.open_by_key(cfg["spreadsheet_id"]), **kwargs)
        except KeyError as exc:
            raise StorageError(f"Missing Google Sheets configuration key: {exc}") from None
        except Exception as exc:  # gspread/google-auth raise many types
            raise StorageError(f"Could not open the spreadsheet: {type(exc).__name__}") from None

    # ---- internals ----------------------------------------------------
    def _call(self, fn, *args, **kwargs):
        import gspread

        for attempt in range(self._retries + 1):
            try:
                return fn(*args, **kwargs)
            except gspread.exceptions.APIError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", 0)
                if status in _RETRYABLE and attempt < self._retries:
                    self._sleep(min(2 ** attempt, 8))
                    continue
                raise StorageError(f"Google Sheets API error (HTTP {status}).") from None
            except Exception as exc:
                if isinstance(exc, (StorageError,)):
                    raise
                raise StorageError(f"Google Sheets request failed: {type(exc).__name__}") from None

    def _worksheet(self, table: str):
        if table not in self._ws:
            import gspread

            try:
                self._ws[table] = self._call(self._ss.worksheet, table)
            except StorageError:
                raise
            except gspread.exceptions.WorksheetNotFound:  # pragma: no cover
                raise SchemaError(f"Worksheet '{table}' is missing.") from None
        return self._ws[table]

    def _read(self, table: str, fresh: bool):
        hit = self._cache.get(table)
        if hit and not fresh and self._clock() - hit[0] < self._ttl:
            return hit[1], hit[2]
        values = self._call(self._worksheet(table).get_all_values)
        if not values:
            raise SchemaError(f"Worksheet '{table}' has no header row.")
        headers = [h.strip() for h in values[0]]
        missing = [h for h in TABLES[table][1] if h not in headers]
        # Payments.reverses_payment_id is optional for sheets created before reversals existed.
        missing = [h for h in missing if not (table == "Payments" and h == "reverses_payment_id")]
        if missing:
            raise SchemaError(f"Worksheet '{table}' is missing columns: {', '.join(missing)}")
        rows = [r for r in values[1:] if any(c.strip() for c in r)]
        self._cache[table] = (self._clock(), headers, rows)
        return headers, rows

    @staticmethod
    def _to_dicts(headers, rows):
        return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)} for r in rows]

    # ---- Repository API ----------------------------------------------
    def list(self, table, *, fresh=False):
        headers, rows = self._read(table, fresh)
        return self._to_dicts(headers, rows)

    def append(self, table, row):
        id_col = TABLES[table][0]
        with self.lock():
            headers, rows = self._read(table, fresh=False)
            idx = headers.index(id_col)
            if any(len(r) > idx and r[idx] == row.get(id_col) for r in rows):
                raise DuplicateError(f"{table}: duplicate {id_col} {row.get(id_col)!r}")
            values = [str(row.get(h, "")) for h in headers]
            self._call(self._worksheet(table).append_row, values,
                       value_input_option="RAW", table_range="A1")
            self.invalidate()

    def update(self, table, record_id, changes, expected_updated_at=None):
        from gspread.utils import rowcol_to_a1

        id_col = TABLES[table][0]
        with self.lock():
            headers, rows = self._read(table, fresh=True)
            idx = headers.index(id_col)
            for offset, r in enumerate(rows):
                if len(r) > idx and r[idx] == record_id:
                    break
            else:
                raise NotFoundError(f"{table}: {record_id} not found")
            current = self._to_dicts(headers, [r])[0]
            if expected_updated_at is not None and current.get("updated_at", "") != expected_updated_at:
                raise ConflictError(f"{table} {record_id} was modified by someone else.")
            current.update({k: str(v) for k, v in changes.items() if k in headers and k != id_col})
            # Row number: rows were filtered of blanks, so locate against the raw sheet instead.
            sheet_row = self._sheet_row(table, idx, record_id)
            end = rowcol_to_a1(sheet_row, len(headers))
            self._call(self._worksheet(table).update, range_name=f"A{sheet_row}:{end}",
                       values=[[current[h] for h in headers]], value_input_option="RAW")
            self.invalidate()
            return current

    def _sheet_row(self, table: str, id_idx: int, record_id: str) -> int:
        col = self._call(self._worksheet(table).col_values, id_idx + 1)
        for n, v in enumerate(col, start=1):
            if n > 1 and v == record_id:
                return n
        raise NotFoundError(f"{table}: {record_id} not found")

    def invalidate(self) -> None:
        self._cache.clear()
