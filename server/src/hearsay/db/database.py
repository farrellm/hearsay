"""The metadata store's connection and queries.

Single-user and low volume: one connection, used from the event loop thread. Queries are
added here by the milestones that need them.
"""

import contextlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from hearsay.db.schema import migrate, schema_version


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        migrate(self.conn)

    def close(self) -> None:
        self.conn.close()

    @property
    def schema_version(self) -> int:
        return schema_version(self.conn)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Several statements as one atomic change (the connection autocommits otherwise)."""
        with self.conn:
            self.conn.execute("BEGIN")
            yield
