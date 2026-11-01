"""The pipeline's stages, the order work is done in, and the runner for the server's own stages
(design.md §3, §5.3).

Each stage of an episode is a row in `tasks`. A runner claims a pending row (marking it
running), does the work and marks it done, or failed: pending again after a backoff, or in
error once the failure can't be fixed by trying again. A row left running by a server that
stopped is pending again at the next start, so the work is redone, never done twice at once.
M1.3 runs the download stage; the later stages arrive with Phase 2.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from enum import IntEnum

import httpx2

from hearsay.db import Database, TaskRow, now
from hearsay.media.download import AudioStore, DownloadError, audio_format, fetch_audio
from hearsay.media.ffmpeg import MediaError, probe_audio

log = logging.getLogger(__name__)

DOWNLOAD = "download"

DOWNLOADS_AT_ONCE = 2
# Waits before each retry; a task fails for good after one more failure than there are waits.
BACKOFF = (
    timedelta(minutes=1),
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
)
TICK_S = 60


class Priority(IntEnum):
    """Higher runs first: Transcribe now > Up Next > new episodes > backfill."""

    BACKFILL = 0
    NEW = 10  # a new episode of an automatic show, or one you picked
    UP_NEXT = 20
    NOW = 30


class Downloads:
    """Runs download tasks, `DOWNLOADS_AT_ONCE` at a time."""

    def __init__(self, db: Database, http: httpx2.AsyncClient, store: AudioStore) -> None:
        self.db = db
        self.http = http
        self.store = store
        self._wake = asyncio.Event()
        self._running: set[asyncio.Task[None]] = set()

    def wake(self) -> None:
        """Look for work now: something was queued."""
        self._wake.set()

    async def run(self) -> None:
        """Claim and run downloads as they become due, forever."""
        if n := self.db.reset_running(DOWNLOAD):
            log.info("resuming %d downloads interrupted by a restart", n)
        try:
            while True:
                self._start_due()
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self._sleep_s())
        finally:
            for task in self._running:
                task.cancel()
            await asyncio.gather(*self._running, return_exceptions=True)

    async def drain(self) -> None:
        """Run every download that is due now, and wait for them (for tests and tools)."""
        self._start_due()
        while self._running:
            await asyncio.gather(*self._running)
            self._start_due()

    def _start_due(self) -> None:
        while len(self._running) < DOWNLOADS_AT_ONCE:
            task = self.db.claim(DOWNLOAD, now())
            if task is None:
                return
            job = asyncio.create_task(self._run(task))
            self._running.add(job)
            job.add_done_callback(self._done)

    def _done(self, job: asyncio.Task[None]) -> None:
        self._running.discard(job)
        self._wake.set()  # a slot is free

    def _sleep_s(self) -> float:
        retry = self.db.next_retry_at(DOWNLOAD)
        if retry is None or len(self._running) >= DOWNLOADS_AT_ONCE:  # a finish wakes us
            return TICK_S
        wait = (datetime.fromisoformat(retry) - datetime.now(UTC)).total_seconds()
        return min(TICK_S, max(wait, 0.1))

    async def _run(self, task: TaskRow) -> None:
        try:
            await self._download(task)
        except (DownloadError, MediaError) as e:
            self._failed(task, str(e), permanent=e.permanent)
        except Exception as e:
            log.exception("download of episode %d failed", task["episode_id"])
            self._failed(task, f"Unexpected error: {e}", permanent=False)

    async def _download(self, task: TaskRow) -> None:
        episode_id = task["episode_id"]
        episode = self.db.episode(episode_id)
        if episode is None:  # unsubscribed meanwhile; the task went with the episode
            self.store.discard_partial(episode_id)
            return
        part = self.store.partial(episode_id)
        fetched = await fetch_audio(self.http, episode["enclosure_url"], part)
        try:
            info = await probe_audio(part)
        except MediaError as e:
            if e.permanent:
                self.store.discard_partial(episode_id)
            raise
        ext, media_type = audio_format(info.codec, info.format)
        path = await asyncio.to_thread(self.store.add, part, fetched.sha256, ext)

        episode = self.db.episode(episode_id)
        if episode is None:  # unsubscribed while downloading
            if not self.db.audio_in_use(path):
                self.store.remove(path)
            return
        old_sha, old_path = episode["audio_sha256"], episode["audio_path"]
        changed = old_sha is not None and old_sha != fetched.sha256
        if changed:
            log.warning(
                "episode %d: the host served different audio than before (%d changes so far);"
                " likely dynamic ad insertion",
                episode_id,
                episode["audio_changes"] + 1,
            )
        with self.db.transaction():
            self.db.update_episode(
                episode_id,
                audio_sha256=fetched.sha256,
                audio_path=path,
                audio_size=fetched.size,
                audio_url=fetched.url,
                audio_type=media_type,
                audio_changes=episode["audio_changes"] + changed,
                duration_s=info.duration_s,
                state="downloaded" if episode["state"] == "discovered" else episode["state"],
                error=None,
            )
            self.db.finish_task(task["id"], input_ref=episode["enclosure_url"])
        if old_path and old_path != path and not self.db.audio_in_use(old_path):
            self.store.remove(old_path)
        log.info(
            "downloaded episode %d (%.1f MB, %s)", episode_id, fetched.size / 1e6, fetched.sha256
        )

    def _failed(self, task: TaskRow, error: str, *, permanent: bool) -> None:
        attempt = task["attempts"]
        retry = None if permanent or attempt >= len(BACKOFF) else BACKOFF[attempt]
        retry_at = (
            None
            if retry is None
            else (datetime.now(UTC) + retry).isoformat(timespec="milliseconds")
        )
        with self.db.transaction():
            self.db.fail_task(task["id"], error, retry_at)
            if retry_at is None:
                self.db.update_episode(task["episode_id"], error=f"Download failed: {error}")
        if retry_at is None:
            log.warning("download of episode %d failed: %s", task["episode_id"], error)
        else:
            log.info(
                "download of episode %d failed (%s); retrying at %s",
                task["episode_id"],
                error,
                retry_at,
            )
