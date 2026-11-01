"""Downloads (M1.3): the task runner, resume, content-addressed storage and probing."""

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient

from hearsay.db import now
from hearsay.services import Services
from hearsay.tasks import DOWNLOAD, Downloads, Priority

from .conftest import Audio, FakeWeb, svc

FEED = "https://pods.example/feed.xml"
AUDIO = "https://cdn.example/1.mp3"


def add_episode(
    s: Services, guid: str, url: str = AUDIO, priority: int | None = Priority.NEW
) -> int:
    feed = s.db.feed_by_url(FEED)
    feed_id = feed["id"] if feed else s.db.insert_feed(FEED, "Gridlock")
    cur = s.db.conn.execute(
        "INSERT INTO episodes (feed_id, guid, title, enclosure_url, duration_s, created_at)"
        " VALUES (?, ?, ?, ?, 3600, ?)",
        [feed_id, guid, guid, url, now()],
    )
    assert cur.lastrowid is not None
    if priority is not None:
        s.db.enqueue(cur.lastrowid, DOWNLOAD, priority)
    return cur.lastrowid


def task(s: Services, episode_id: int) -> dict[str, object]:
    row = s.db.conn.execute(
        "SELECT status, attempts, not_before, last_error FROM tasks WHERE episode_id = ?",
        [episode_id],
    ).fetchone()
    return dict(row)


async def wait_for(condition: Callable[[], bool], timeout: float = 5) -> None:
    """Until `condition` holds: a background runner's effect, seen in the database."""
    async with asyncio.timeout(timeout):
        while not condition():  # noqa: ASYNC110 - nothing to wait on but the database
            await asyncio.sleep(0.01)


async def test_download_is_stored_by_hash_and_probed(
    client: TestClient, web: FakeWeb, mp3: bytes
) -> None:
    s = svc(client)
    web.redirect(AUDIO, "https://edge.example/1.mp3", status=302)
    web.audio("https://edge.example/1.mp3", Audio(mp3))
    ep = add_episode(s, "a")
    await s.downloads.drain()

    sha = hashlib.sha256(mp3).hexdigest()
    episode = s.db.episode(ep)
    assert episode is not None
    assert episode["state"] == "downloaded"
    assert episode["audio_sha256"] == sha
    assert episode["audio_path"] == f"audio/{sha[:2]}/{sha}.mp3"
    assert episode["audio_size"] == len(mp3)
    assert episode["audio_url"] == "https://edge.example/1.mp3"
    assert episode["audio_type"] == "audio/mpeg"
    assert episode["duration_s"] == pytest.approx(3, abs=0.1)  # probed, not the feed's 3600
    assert s.audio.path(episode["audio_path"]).read_bytes() == mp3
    assert list(s.audio.partials.iterdir()) == []
    assert task(s, ep)["status"] == "done"
    # The show's page marks it downloaded.
    [summary] = client.get(f"/api/feeds/{episode['feed_id']}").json()["episodes"]
    assert (summary["state"], summary["task"], summary["error"]) == ("downloaded", "done", None)


async def test_aac_is_stored_as_m4a(client: TestClient, web: FakeWeb, m4a: bytes) -> None:
    s = svc(client)
    web.audio(AUDIO, Audio(m4a))
    ep = add_episode(s, "a")
    await s.downloads.drain()
    episode = s.db.episode(ep)
    assert episode is not None
    assert episode["audio_path"] is not None
    assert episode["audio_path"].endswith(".m4a")
    assert episode["audio_type"] == "audio/mp4"


async def test_restart_mid_download_resumes_and_runs_the_task_once(
    client: TestClient, web: FakeWeb, mp3: bytes
) -> None:
    """M1.3's check: the server killed mid-download finishes the file, with the right sha256,
    on its next start."""
    s = svc(client)
    route = web.audio(AUDIO, Audio(mp3, hold_after=len(mp3) // 2))
    ep = add_episode(s, "a")

    runner = asyncio.create_task(s.downloads.run())
    await asyncio.wait_for(route.held.wait(), 5)
    runner.cancel()  # the server stops
    with pytest.raises(asyncio.CancelledError):
        await runner
    have = s.audio.partial(ep).stat().st_size
    assert 0 < have < len(mp3)
    assert task(s, ep)["status"] == "running"  # as a killed server leaves it

    route.release.set()
    restarted = Downloads(s.db, s.http, s.audio)  # a new server process
    runner = asyncio.create_task(restarted.run())
    await wait_for(lambda: task(s, ep)["status"] == "done")
    runner.cancel()

    episode = s.db.episode(ep)
    assert episode is not None
    assert episode["audio_sha256"] == hashlib.sha256(mp3).hexdigest()
    resumed = web.requests[-1]
    assert resumed.headers["range"] == f"bytes={have}-"
    assert resumed.headers["if-range"] == '"a1"'
    assert route.sent == len(mp3)  # nothing was fetched twice
    assert task(s, ep)["attempts"] == 0
    assert web.fetched(AUDIO) == 2

    await restarted.drain()
    assert web.fetched(AUDIO) == 2  # done stays done


async def test_changed_file_starts_over(client: TestClient, web: FakeWeb, mp3: bytes) -> None:
    """A partial download of other bytes (a new ad cut: a new ETag) is never joined to them."""
    s = svc(client)
    route = web.audio(AUDIO, Audio(b"x" * 5000 + mp3, etag='"old"', hold_after=4096))
    ep = add_episode(s, "a")
    runner = asyncio.create_task(s.downloads.run())
    await asyncio.wait_for(route.held.wait(), 5)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    web.audio(AUDIO, Audio(mp3, etag='"new"'))
    s.db.reset_running(DOWNLOAD)
    await s.downloads.drain()
    episode = s.db.episode(ep)
    assert episode is not None
    assert episode["audio_sha256"] == hashlib.sha256(mp3).hexdigest()
    assert web.requests[-1].headers["if-range"] == '"old"'


async def test_host_without_ranges_starts_over(
    client: TestClient, web: FakeWeb, mp3: bytes
) -> None:
    s = svc(client)
    route = web.audio(AUDIO, Audio(mp3, etag=None, ranges=False, hold_after=len(mp3) // 2))
    ep = add_episode(s, "a")
    runner = asyncio.create_task(s.downloads.run())
    await asyncio.wait_for(route.held.wait(), 5)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    route.release.set()
    s.db.reset_running(DOWNLOAD)
    await s.downloads.drain()
    episode = s.db.episode(ep)
    assert episode is not None
    assert episode["audio_sha256"] == hashlib.sha256(mp3).hexdigest()
    assert s.audio.path(episode["audio_path"] or "").read_bytes() == mp3


async def test_two_downloads_at_once_by_priority(
    client: TestClient, web: FakeWeb, mp3: bytes
) -> None:
    s = svc(client)
    routes = {}
    for name, priority in (("backfill", Priority.BACKFILL), ("new", Priority.NEW)):
        url = f"https://cdn.example/{name}.mp3"
        routes[name] = web.audio(url, Audio(mp3, hold_after=0))
        add_episode(s, name, url, priority)
    now_url = "https://cdn.example/now.mp3"
    routes["now"] = web.audio(now_url, Audio(mp3, hold_after=0))
    add_episode(s, "now", now_url, Priority.NOW)

    runner = asyncio.create_task(s.downloads.run())
    await asyncio.wait_for(routes["now"].held.wait(), 5)
    await asyncio.wait_for(routes["new"].held.wait(), 5)
    await asyncio.sleep(0.05)
    assert web.fetched("https://cdn.example/backfill.mp3") == 0  # two at a time

    for route in routes.values():
        route.release.set()
    await wait_for(
        lambda: {r[0] for r in s.db.conn.execute("SELECT status FROM tasks")} == {"done"}
    )
    runner.cancel()
    # Same bytes, one file.
    assert len(list(s.audio.root.glob("*/*.mp3"))) == 1


async def test_failures_retry_then_stop(client: TestClient, web: FakeWeb, mp3: bytes) -> None:
    s = svc(client)
    web.routes[AUDIO] = lambda _r: httpx2.Response(503)
    ep = add_episode(s, "a")
    await s.downloads.drain()
    t = task(s, ep)
    assert (t["status"], t["attempts"]) == ("pending", 1)
    assert t["last_error"] == "cdn.example answered HTTP 503 for the audio."
    retry_at = datetime.fromisoformat(str(t["not_before"]))
    assert timedelta(seconds=50) < retry_at - datetime.now(UTC) < timedelta(seconds=70)
    await s.downloads.drain()
    assert task(s, ep)["attempts"] == 1  # not due yet

    def due() -> None:
        s.db.conn.execute("UPDATE tasks SET not_before = ?", [now()])

    for _ in range(4):
        due()
        await s.downloads.drain()
    t = task(s, ep)
    assert (t["status"], t["attempts"]) == ("error", 5)
    episode = s.db.episode(ep)
    assert episode is not None
    assert episode["error"] == "Download failed: cdn.example answered HTTP 503 for the audio."

    # Transcribe (Try again) queues it afresh.
    web.audio(AUDIO, Audio(mp3))
    assert client.post(f"/api/episodes/{ep}/process").status_code == 204
    assert task(s, ep)["status"] == "pending"
    assert task(s, ep)["attempts"] == 0
    await s.downloads.drain()
    assert task(s, ep)["status"] == "done"
    episode = s.db.episode(ep)
    assert episode is not None
    assert episode["error"] is None


async def test_permanent_failures_stop_at_once(client: TestClient, web: FakeWeb) -> None:
    s = svc(client)
    gone = add_episode(s, "gone", "https://cdn.example/gone.mp3")
    web.routes["https://cdn.example/page.mp3"] = lambda _r: httpx2.Response(
        200, text="<html>Not here</html>", headers={"content-type": "text/html"}
    )
    page = add_episode(s, "page", "https://cdn.example/page.mp3")
    await s.downloads.drain()

    assert task(s, gone)["status"] == "error"
    assert task(s, gone)["last_error"] == "cdn.example answered HTTP 404 for the audio."
    assert task(s, page)["status"] == "error"
    assert task(s, page)["last_error"] == "The file isn't audio."
    assert list(s.audio.partials.iterdir()) == []
    assert list(s.audio.root.glob("??/*")) == []


async def test_unsubscribing_drops_queued_downloads(
    client: TestClient, web: FakeWeb, mp3: bytes
) -> None:
    s = svc(client)
    web.audio(AUDIO, Audio(mp3))
    ep = add_episode(s, "a")
    episode = s.db.episode(ep)
    assert episode is not None
    s.db.delete_feed(episode["feed_id"])
    await s.downloads.drain()
    assert web.fetched(AUDIO) == 0


def test_moved_enclosure_keeps_the_download(client: TestClient) -> None:
    s = svc(client)
    ep = add_episode(s, "a", priority=None)
    feed_id = s.db.conn.execute("SELECT feed_id FROM episodes").fetchone()[0]
    s.db.update_episode(ep, audio_sha256="ab" * 32, audio_path="audio/ab/x.mp3")
    fields = {
        "guid": "a",
        "title": "a",
        "description": None,
        "link": None,
        "published_at": None,
        "enclosure_url": "https://cdn2.example/1.mp3",
        "enclosure_type": "audio/mpeg",
        "enclosure_length": None,
        "duration_s": 99.0,
        "image": None,
        "chapters_url": None,
        "feed_transcript_url": None,
        "feed_transcript_type": None,
        "persons": [],
    }
    assert s.db.upsert_episode(feed_id, fields) == (ep, False, True)  # type: ignore[arg-type]
    episode = s.db.episode(ep)
    assert episode is not None
    assert episode["audio_sha256"] == "ab" * 32
    assert episode["duration_s"] == 3600  # the probed length stays
