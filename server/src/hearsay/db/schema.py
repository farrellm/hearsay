"""The metadata store's tables (design.md §7.2), as numbered migrations.

`PRAGMA user_version` records how many migrations a database has had. Each migration runs in
its own transaction with the version bump, so a failed one leaves the database as it was.
Never edit a migration that has shipped: append a new one. Tables that later phases need
(FTS5, sqlite-vec chunks, items and entities) arrive as their own migrations then.
"""

import sqlite3

# Times are ISO 8601 UTC text (`hearsay.db.now()`); JSON columns hold JSON text.
MIGRATIONS: tuple[str, ...] = (
    # 1: Phases 1 and 2 core: feeds, episodes, the task queue, transcripts, playback, sessions.
    """
    CREATE TABLE feeds (
        id INTEGER PRIMARY KEY,
        url TEXT NOT NULL UNIQUE,
        title TEXT,
        author TEXT,
        description TEXT,
        link TEXT,
        image TEXT,
        language TEXT,
        etag TEXT,
        last_modified TEXT,
        last_polled_at TEXT,
        poll_interval_min INTEGER,
        policy TEXT NOT NULL DEFAULT 'auto' CHECK (policy IN ('auto', 'manual')),
        backfill_n INTEGER NOT NULL DEFAULT 10,
        diarize INTEGER NOT NULL DEFAULT 1,
        language_override TEXT,
        speaker_map TEXT NOT NULL DEFAULT '{}',
        trust_feed_transcript INTEGER NOT NULL DEFAULT 0,
        default_speed REAL,
        audio_keep_days INTEGER DEFAULT 30,
        error TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE episodes (
        id INTEGER PRIMARY KEY,
        feed_id INTEGER NOT NULL REFERENCES feeds (id) ON DELETE CASCADE,
        guid TEXT NOT NULL,
        title TEXT,
        description TEXT,
        link TEXT,
        published_at TEXT,
        enclosure_url TEXT NOT NULL,
        enclosure_type TEXT,
        enclosure_length INTEGER,
        duration_s REAL,
        audio_sha256 TEXT,
        audio_path TEXT,
        audio_size INTEGER,
        chapters TEXT,
        feed_transcript_url TEXT,
        image TEXT,
        state TEXT NOT NULL DEFAULT 'discovered',
        error TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (feed_id, guid)
    );
    CREATE INDEX episodes_feed_published ON episodes (feed_id, published_at DESC);

    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY,
        gpu TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ready_at TEXT,
        ended_at TEXT,
        end_reason TEXT,
        unit_rate REAL,
        audio_minutes_processed REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE tasks (
        id INTEGER PRIMARY KEY,
        episode_id INTEGER NOT NULL REFERENCES episodes (id) ON DELETE CASCADE,
        stage TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'running', 'done', 'error')),
        priority INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        not_before TEXT,
        last_error TEXT,
        input_ref TEXT,
        session_id INTEGER REFERENCES sessions (id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    -- At most one unfinished task per episode and stage.
    CREATE UNIQUE INDEX tasks_open ON tasks (episode_id, stage) WHERE status != 'done';
    CREATE INDEX tasks_runnable ON tasks (stage, status, priority DESC, created_at);

    CREATE TABLE transcripts (
        id INTEGER PRIMARY KEY,
        episode_id INTEGER NOT NULL REFERENCES episodes (id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        engine TEXT NOT NULL,
        language TEXT,
        path TEXT NOT NULL,
        word_count INTEGER,
        created_at TEXT NOT NULL,
        is_current INTEGER NOT NULL DEFAULT 0,
        UNIQUE (episode_id, version)
    );
    CREATE UNIQUE INDEX transcripts_current ON transcripts (episode_id) WHERE is_current;

    -- An integer rowid, so the FTS5 index (M3.1) can use this as its external content.
    CREATE TABLE segments (
        id INTEGER PRIMARY KEY,
        transcript_id INTEGER NOT NULL REFERENCES transcripts (id) ON DELETE CASCADE,
        i INTEGER NOT NULL,
        start REAL NOT NULL,
        "end" REAL NOT NULL,
        speaker TEXT,
        text TEXT NOT NULL,
        UNIQUE (transcript_id, i)
    );

    CREATE TABLE speakers (
        transcript_id INTEGER NOT NULL REFERENCES transcripts (id) ON DELETE CASCADE,
        label TEXT NOT NULL,
        name TEXT,
        guessed INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (transcript_id, label)
    );

    CREATE TABLE playback (
        episode_id INTEGER PRIMARY KEY REFERENCES episodes (id) ON DELETE CASCADE,
        position_s REAL NOT NULL DEFAULT 0,
        speed REAL,
        finished INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE queue (
        episode_id INTEGER PRIMARY KEY REFERENCES episodes (id) ON DELETE CASCADE,
        position INTEGER NOT NULL
    );

    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
)

SCHEMA_VERSION = len(MIGRATIONS)


def schema_version(conn: sqlite3.Connection) -> int:
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    return version


def migrate(conn: sqlite3.Connection) -> int:
    """Apply the migrations `conn`'s database hasn't had. Returns the resulting version.

    `conn` must be in autocommit mode (`isolation_level=None`), so each migration's own
    BEGIN/COMMIT is the only transaction.
    """
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        msg = (
            f"Database schema version {current} is newer than this code knows "
            f"({SCHEMA_VERSION}). Update Hearsay before opening it."
        )
        raise RuntimeError(msg)
    for version, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        # executescript would commit between statements; run it inside one explicit transaction.
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return schema_version(conn)
