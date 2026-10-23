"""Google Sheets implementation of Repository (gspread).

Notes
- Reads use get_all_values() so IDs, phone numbers and money stay as text (no type coercion).
- Writes use value_input_option="RAW" so values are never interpreted as formulas.
- Reads are cached per worksheet for `ttl` seconds and invalidated after every write.
- list_many() fetches every stale worksheet in ONE batch API call (the snapshot loader uses it).
- Updates read the worksheet fresh once (rows keep their sheet row numbers) for the optimistic-concurrency
  check, then write; appends reuse cached headers and do not need a read at all.
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
        # table -> (fetched_at, headers, rows, sheet row numbers of those rows)
        self._cache: dict[str, tuple[float, list[str], list[list[str]], list[int]]] = {}
        self._headers: dict[str, tuple[float, list[str]]] = {}  # survives invalidate(); short-lived
        self._ws: dict = {}

    # ---- construction -------------------------------------------------
    @classmethod
    def from_config(cls, cfg: Mapping, **kwargs) -> "GoogleSheetsRepository":
        """cfg: {'spreadsheet_id': ..., 'service_account': {...service account JSON fields...}}"""
        import gspread  # imported lazily so tests do not need credentials

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

    def _parse_values(self, table: str, values: list[list[str]]):
        """Validate the header row and drop blank rows, remembering each row's sheet row number."""
        if not values:
            raise SchemaError(f"Worksheet '{table}' has no header row.")
        headers = [h.strip() for h in values[0]]
        missing = [h for h in TABLES[table][1] if h not in headers]
        if missing:
            raise SchemaError(f"Worksheet '{table}' is missing columns: {', '.join(missing)}")
        rows, numbers = [], []
        for n, r in enumerate(values[1:], start=2):
            if any(c.strip() for c in r):
                rows.append(r)
                numbers.append(n)
        return headers, rows, numbers

    def _store(self, table: str, values: list[list[str]]) -> None:
        headers, rows, numbers = self._parse_values(table, values)
        now = self._clock()
        self._cache[table] = (now, headers, rows, numbers)
        self._headers[table] = (now, headers)

    def _is_fresh(self, table: str) -> bool:
        hit = self._cache.get(table)
        return bool(hit) and self._clock() - hit[0] < self._ttl

    def _read(self, table: str, fresh: bool):
        if fresh or not self._is_fresh(table):
            self._store(table, self._call(self._worksheet(table).get_all_values))
        _, headers, rows, numbers = self._cache[table]
        return headers, rows, numbers

    def _known_headers(self, table: str) -> list[str]:
        """Headers for appends: cached for 5 minutes so an append does not cost a read."""
        hit = self._headers.get(table)
        if hit and self._clock() - hit[0] < 300:
            return hit[1]
        return self._read(table, fresh=True)[0]

    @staticmethod
    def _to_dicts(headers, rows):
        return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)} for r in rows]

    # ---- Repository API ----------------------------------------------
    def list(self, table, *, fresh=False):
        headers, rows, _ = self._read(table, fresh)
        return self._to_dicts(headers, rows)

    def list_many(self, tables, *, fresh=False):
        """Read several worksheets, fetching every stale one in a single batch request."""
        stale = [t for t in tables if fresh or not self._is_fresh(t)]
        if stale:
            try:
                response = self._call(self._ss.values_batch_get, [f"'{t}'" for t in stale])
            except StorageError as exc:
                if "HTTP 400" in str(exc):  # Google rejects a range whose worksheet does not exist
                    raise SchemaError("A required worksheet is missing or misnamed. Expected tabs: "
                                      + ", ".join(stale)) from None
                raise
            ranges = response.get("valueRanges", [])
            if len(ranges) != len(stale):
                raise StorageError("Google Sheets returned an unexpected response.")
            for table, value_range in zip(stale, ranges):
                self._store(table, value_range.get("values", []))
        return {t: self._to_dicts(self._cache[t][1], self._cache[t][2]) for t in tables}

    def append(self, table, row):
        id_col = TABLES[table][0]
        with self.lock():
            headers = self._known_headers(table)
            hit = self._cache.get(table)  # duplicate check only against rows already in memory (ids are random)
            if hit and id_col in headers:
                idx = headers.index(id_col)
                if any(len(r) > idx and r[idx] == row.get(id_col) for r in hit[2]):
                    raise DuplicateError(f"{table}: duplicate {id_col} {row.get(id_col)!r}")
            values = [str(row.get(h, "")) for h in headers]
            self._call(self._worksheet(table).append_row, values,
                       value_input_option="RAW", table_range="A1")
            self.invalidate()

    def update(self, table, record_id, changes, expected_updated_at=None):
        from gspread.utils import rowcol_to_a1

        id_col = TABLES[table][0]
        with self.lock():
            headers, rows, numbers = self._read(table, fresh=True)  # the only read: rows carry their sheet row numbers
            idx = headers.index(id_col)
            for position, r in enumerate(rows):
                if len(r) > idx and r[idx] == record_id:
                    break
            else:
                raise NotFoundError(f"{table}: {record_id} not found")
            current = self._to_dicts(headers, [r])[0]
            if expected_updated_at is not None and current.get("updated_at", "") != expected_updated_at:
                raise ConflictError(f"{table} {record_id} was modified by someone else.")
            current.update({k: str(v) for k, v in changes.items() if k in headers and k != id_col})
            sheet_row = numbers[position]
            end = rowcol_to_a1(sheet_row, len(headers))
            self._call(self._worksheet(table).update, range_name=f"A{sheet_row}:{end}",
                       values=[[current[h] for h in headers]], value_input_option="RAW")
            self.invalidate()
            return current

    def invalidate(self) -> None:
        self._cache.clear()
