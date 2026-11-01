import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from hearsay.db import SCHEMA_VERSION, Database, now
from hearsay.db.schema import MIGRATIONS, migrate


def test_fresh_database_is_fully_migrated(tmp_path: Path) -> None:
    db = Database(tmp_path / "h.db")
    assert db.schema_version == SCHEMA_VERSION == len(MIGRATIONS)
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"feeds", "episodes", "tasks", "transcripts", "segments", "playback"} <= tables
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.close()


def test_reopening_is_a_no_op(tmp_path: Path) -> None:
    Database(tmp_path / "h.db").close()
    db = Database(tmp_path / "h.db")
    assert db.schema_version == SCHEMA_VERSION
    db.close()


def test_newer_database_is_refused(tmp_path: Path) -> None:
    with closing(sqlite3.connect(tmp_path / "h.db", isolation_level=None)) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        with pytest.raises(RuntimeError, match="newer"):
            migrate(conn)


def test_failed_migration_leaves_the_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = (*MIGRATIONS, "CREATE TABLE extra (x); CREATE TABLE feeds (y);")
    monkeypatch.setattr("hearsay.db.schema.MIGRATIONS", broken)
    monkeypatch.setattr("hearsay.db.schema.SCHEMA_VERSION", len(broken))
    with closing(sqlite3.connect(tmp_path / "h.db", isolation_level=None)) as conn:
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn)
        assert not conn.in_transaction
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "extra" not in names


def test_foreign_keys_cascade(tmp_path: Path) -> None:
    db = Database(tmp_path / "h.db")
    t = now()
    db.conn.execute("INSERT INTO feeds (id, url, created_at) VALUES (1, 'https://x/feed', ?)", [t])
    db.conn.execute(
        "INSERT INTO episodes (feed_id, guid, enclosure_url, created_at) VALUES (1, 'g', 'u', ?)",
        [t],
    )
    db.conn.execute("DELETE FROM feeds WHERE id = 1")
    assert db.conn.execute("SELECT count(*) FROM episodes").fetchone()[0] == 0
    db.close()


def test_one_open_task_per_episode_and_stage(tmp_path: Path) -> None:
    db = Database(tmp_path / "h.db")
    t = now()
    db.conn.execute("INSERT INTO feeds (id, url, created_at) VALUES (1, 'https://x/feed', ?)", [t])
    db.conn.execute(
        "INSERT INTO episodes (id, feed_id, guid, enclosure_url, created_at)"
        " VALUES (1, 1, 'g', 'u', ?)",
        [t],
    )
    insert = (
        "INSERT INTO tasks (episode_id, stage, status, created_at, updated_at)"
        " VALUES (1, 'download', ?, ?, ?)"
    )
    db.conn.execute(insert, ["done", t, t])
    db.conn.execute(insert, ["pending", t, t])
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(insert, ["error", t, t])
    db.close()
