"""The metadata store's connection and queries.

Single-user and low volume: one connection, used from the event loop thread. Queries are
added here by the milestones that need them.
"""

import contextlib
import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Unpack, cast

from hearsay.db.rows import (
    EpisodeFields,
    EpisodeSummary,
    FeedRow,
    FeedSummary,
    FeedUpdate,
    ImageRow,
    Policy,
)
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

    def _update(self, table: str, id_: int, fields: Mapping[str, Any]) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE {table} SET {cols} WHERE id = ?", [*fields.values(), id_])  # noqa: S608

    # -- feeds -----------------------------------------------------------------------------

    def feed(self, id_: int) -> FeedRow | None:
        row = self.conn.execute("SELECT * FROM feeds WHERE id = ?", [id_]).fetchone()
        return _feed(row)

    def feed_by_url(self, url: str) -> FeedRow | None:
        row = self.conn.execute("SELECT * FROM feeds WHERE url = ?", [url]).fetchone()
        return _feed(row)

    def insert_feed(
        self, url: str, title: str | None = None, policy: Policy = "auto", backfill_n: int = 10
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO feeds (url, title, policy, backfill_n, created_at) VALUES (?, ?, ?, ?, ?)",
            [url, title, policy, backfill_n, now()],
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def update_feed(self, id_: int, **fields: Unpack[FeedUpdate]) -> None:
        self._update("feeds", id_, fields)

    def delete_feed(self, id_: int) -> None:
        self.conn.execute("DELETE FROM feeds WHERE id = ?", [id_])

    def feeds_due(self, at: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT id FROM feeds WHERE next_poll_at IS NULL OR next_poll_at <= ?"
            " ORDER BY next_poll_at IS NOT NULL, next_poll_at",
            [at],
        )
        return [r[0] for r in rows]

    def feeds(self) -> list[FeedSummary]:
        """Every show, newest episode first (ui.md §4.2)."""
        rows = self.conn.execute(
            """
            SELECT f.id, f.url, f.title, f.author, i.sha256 AS artwork, f.policy, f.error,
                (SELECT count(*) FROM episodes e WHERE e.feed_id = f.id) AS episode_count,
                (SELECT count(*) FROM episodes e
                    LEFT JOIN playback p ON p.episode_id = e.id
                    WHERE e.feed_id = f.id AND NOT coalesce(p.finished, 0)
                    AND (e.created_at > f.first_polled_at
                         OR EXISTS (SELECT 1 FROM tasks t WHERE t.episode_id = e.id))
                ) AS unplayed,
                n.title AS newest_title, n.published_at AS newest_published_at
            FROM feeds f
            LEFT JOIN images i ON i.url = f.image
            LEFT JOIN episodes n ON n.id = (
                SELECT id FROM episodes WHERE feed_id = f.id
                ORDER BY published_at DESC, id DESC LIMIT 1)
            ORDER BY n.published_at IS NULL, n.published_at DESC, f.title COLLATE NOCASE
            """
        )
        return _rows(rows, FeedSummary)

    def newest_published_at(self, feed_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT max(published_at) FROM episodes WHERE feed_id = ?", [feed_id]
        ).fetchone()
        value: str | None = row[0]
        return value

    # -- episodes --------------------------------------------------------------------------

    def upsert_episode(self, feed_id: int, ep: EpisodeFields) -> tuple[int, bool]:
        """Insert or refresh an episode by its guid. Returns its id and whether it is new.

        A changed enclosure URL keeps the episode's identity (design.md §4). The feed's
        duration is replaced only until the file itself has been probed.
        """
        values = {**ep, "persons": json.dumps(ep["persons"]) if ep["persons"] else None}
        row = self.conn.execute(
            "SELECT id FROM episodes WHERE feed_id = ? AND guid = ?", [feed_id, ep["guid"]]
        ).fetchone()
        if row is None:
            cols = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            cur = self.conn.execute(
                f"INSERT INTO episodes (feed_id, {cols}, created_at) VALUES (?, {marks}, ?)",  # noqa: S608
                [feed_id, *values.values(), now()],
            )
            assert cur.lastrowid is not None
            return cur.lastrowid, True
        id_: int = row[0]
        del values["guid"]
        duration = values.pop("duration_s")
        self._update("episodes", id_, values)
        self.conn.execute(
            "UPDATE episodes SET duration_s = ? WHERE id = ? AND audio_sha256 IS NULL",
            [duration, id_],
        )
        return id_, False

    def newest_episodes(self, feed_id: int, n: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT id FROM episodes WHERE feed_id = ? ORDER BY published_at DESC, id DESC LIMIT ?",
            [feed_id, n],
        )
        return [r[0] for r in rows]

    def episode_exists(self, id_: int) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM episodes WHERE id = ?", [id_]).fetchone() is not None
        )

    def feed_episodes(self, feed_id: int) -> list[EpisodeSummary]:
        rows = self.conn.execute(
            """
            SELECT e.id, e.title, e.published_at, e.duration_s, e.state,
                coalesce(ei.sha256, fi.sha256) AS artwork,
                (SELECT t.status FROM tasks t WHERE t.episode_id = e.id AND t.stage = 'download'
                    ORDER BY t.id DESC LIMIT 1) AS task,
                coalesce(p.finished, 0) AS finished
            FROM episodes e
            JOIN feeds f ON f.id = e.feed_id
            LEFT JOIN images ei ON ei.url = e.image
            LEFT JOIN images fi ON fi.url = f.image
            LEFT JOIN playback p ON p.episode_id = e.id
            WHERE e.feed_id = ?
            ORDER BY e.published_at DESC, e.id DESC
            """,
            [feed_id],
        )
        episodes = _rows(rows, EpisodeSummary)
        for e in episodes:
            e["finished"] = bool(e["finished"])
        return episodes

    def episode_images(self, feed_id: int, n: int) -> list[str]:
        """The distinct artwork of the newest `n` episodes."""
        rows = self.conn.execute(
            "SELECT DISTINCT image FROM (SELECT image FROM episodes WHERE feed_id = ?"
            " ORDER BY published_at DESC, id DESC LIMIT ?) WHERE image IS NOT NULL",
            [feed_id, n],
        )
        return [r[0] for r in rows]

    # -- tasks -----------------------------------------------------------------------------

    def enqueue(self, episode_id: int, stage: str, priority: int) -> bool:
        """Queue a stage for an episode unless it already has that task, in any status; a
        pending one is raised to `priority` if that is higher. Returns whether one was added."""
        ts = now()
        cur = self.conn.execute(
            "INSERT INTO tasks (episode_id, stage, priority, created_at, updated_at)"
            " SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS"
            " (SELECT 1 FROM tasks WHERE episode_id = ? AND stage = ?)",
            [episode_id, stage, priority, ts, ts, episode_id, stage],
        )
        if cur.rowcount:
            return True
        self.conn.execute(
            "UPDATE tasks SET priority = ?, updated_at = ? WHERE episode_id = ? AND stage = ?"
            " AND status = 'pending' AND priority < ?",
            [priority, ts, episode_id, stage, priority],
        )
        return False

    # -- images ----------------------------------------------------------------------------

    def image(self, url: str) -> ImageRow | None:
        row = self.conn.execute("SELECT * FROM images WHERE url = ?", [url]).fetchone()
        return None if row is None else cast("ImageRow", dict(row))

    def save_image(self, url: str, sha256: str | None, fetched_at: str, error: str | None) -> None:
        self.conn.execute(
            "INSERT INTO images (url, sha256, fetched_at, error) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (url) DO UPDATE SET"
            " sha256 = excluded.sha256, fetched_at = excluded.fetched_at, error = excluded.error",
            [url, sha256, fetched_at, error],
        )


def _rows[T](rows: Iterable[sqlite3.Row], kind: type[T]) -> list[T]:
    """Fetched rows as their TypedDict (`kind` is only for the type checker)."""
    return [cast("T", dict(row)) for row in rows]


def _feed(row: sqlite3.Row | None) -> FeedRow | None:
    if row is None:
        return None
    feed = cast("FeedRow", dict(row))
    feed["diarize"] = bool(feed["diarize"])
    return feed
