"""Storage abstraction. Swap GoogleSheetsRepository for a database by implementing Repository."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager

from core.models import TABLES


class StorageError(RuntimeError):
    """Backend unavailable or failed (e.g. Google Sheets API error)."""


class SchemaError(StorageError):
    """A worksheet is missing or its headers do not match the expected schema."""


class DuplicateError(ValueError):
    pass


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    """The record changed since it was read (optimistic concurrency check failed)."""


class Repository(ABC):
    """Rows are dicts of strings keyed by header. Tables and id columns are defined in TABLES."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    @contextmanager
    def lock(self):
        """Serialise read-validate-write sequences within this process."""
        with self._lock:
            yield

    @abstractmethod
    def list(self, table: str, *, fresh: bool = False) -> list[dict]: ...

    @abstractmethod
    def append(self, table: str, row: dict) -> None: ...

    @abstractmethod
    def update(self, table: str, record_id: str, changes: dict,
               expected_updated_at: str | None = None) -> dict: ...

    def invalidate(self) -> None:  # pragma: no cover - default no-op
        pass

    def get(self, table: str, record_id: str, *, fresh: bool = False) -> dict | None:
        id_col = TABLES[table][0]
        return next((r for r in self.list(table, fresh=fresh) if r.get(id_col) == record_id), None)


class InMemoryRepository(Repository):
    """Used by tests and the credential-free demo mode."""

    def __init__(self) -> None:
        super().__init__()
        self._data: dict[str, list[dict]] = {t: [] for t in TABLES}
        self.fail_with: Exception | None = None  # test hook to simulate API failures

    def _check(self) -> None:
        if self.fail_with:
            raise self.fail_with

    def list(self, table, *, fresh=False):
        self._check()
        return [dict(r) for r in self._data[table]]

    def append(self, table, row):
        self._check()
        id_col, headers = TABLES[table]
        if any(r[id_col] == row.get(id_col) for r in self._data[table]):
            raise DuplicateError(f"{table}: duplicate {id_col} {row.get(id_col)!r}")
        self._data[table].append({h: str(row.get(h, "")) for h in headers})

    def update(self, table, record_id, changes, expected_updated_at=None):
        self._check()
        id_col = TABLES[table][0]
        for r in self._data[table]:
            if r[id_col] == record_id:
                if expected_updated_at is not None and r.get("updated_at", "") != expected_updated_at:
                    raise ConflictError(f"{table} {record_id} was modified by someone else.")
                r.update({k: str(v) for k, v in changes.items() if k in r and k != id_col})
                return dict(r)
        raise NotFoundError(f"{table}: {record_id} not found")
