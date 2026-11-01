"""Subscriptions and the poller (design.md §4).

Each feed is fetched on its interval with conditional GET, and new episodes are queued for
download according to the feed's policy. The first successful poll of a feed queues its
newest `backfill_n` episodes instead, so subscribing never queues a whole back catalog.
"""

import asyncio
import contextlib
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2

from hearsay.db import Database, EpisodeFields, FeedRow, Policy, now
from hearsay.db.rows import PersonRow
from hearsay.feeds.fetch import Fetched, check_url, fetch_feed
from hearsay.feeds.opml import Outline, export_opml, parse_opml
from hearsay.feeds.parser import FeedError, ParsedEpisode, ParsedFeed, parse_feed
from hearsay.media.images import ImageCache
from hearsay.tasks import DOWNLOAD, Priority

log = logging.getLogger(__name__)

DEFAULT_INTERVAL = timedelta(minutes=60)
DORMANT_AFTER = timedelta(days=90)  # feeds quiet for this long are polled daily
DORMANT_INTERVAL = timedelta(days=1)
JITTER = 0.1  # ± this fraction of the interval, so polls don't bunch up
# An episode first seen on a later poll is only "new" if it's this recent; a host that
# republishes its back catalog shouldn't queue all of it.
NEW_EPISODE_MAX_AGE = timedelta(days=30)
TICK_S = 60
CONCURRENCY = 4
EPISODE_IMAGES = 20  # artwork is cached for the newest episodes only


class AlreadySubscribedError(FeedError):
    pass


@dataclass
class Preview:
    """What the Add a show sheet shows before you subscribe (ui.md §4.2)."""

    url: str
    title: str
    author: str | None
    image: str | None
    feed_id: int | None  # set when you already subscribe to it
    durations: list[float | None]  # each episode's, newest first, for the backfill estimate


@dataclass
class Imported:
    added: int
    skipped: int


class Feeds:
    def __init__(
        self,
        db: Database,
        http: httpx2.AsyncClient,
        images: ImageCache,
        rng: random.Random | None = None,
    ) -> None:
        self.db = db
        self.http = http
        self.images = images
        self.rng = rng or random.Random()  # noqa: S311 (jitter, not secrets)
        self._wake = asyncio.Event()
        self._locks: dict[int, asyncio.Lock] = {}
        self._limit = asyncio.Semaphore(CONCURRENCY)
        self._background: set[asyncio.Task[None]] = set()

    async def aclose(self) -> None:
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)

    # -- subscribing -----------------------------------------------------------------------

    async def preview(self, url: str) -> Preview:
        url = check_url(url)
        fetched = await fetch_feed(self.http, url)
        url = fetched.moved_to or url
        assert fetched.body is not None
        parsed = parse_feed(fetched.body)
        existing = self.db.feed_by_url(url)
        return Preview(
            url=url,
            title=parsed.title,
            author=parsed.author,
            image=parsed.image,
            feed_id=existing["id"] if existing else None,
            durations=[e.duration_s for e in _newest_first(parsed.episodes)],
        )

    async def subscribe(self, url: str, policy: Policy, backfill_n: int) -> FeedRow:
        url = check_url(url)
        self._check_new(url)
        fetched = await fetch_feed(self.http, url)
        if fetched.moved_to:
            url = fetched.moved_to
            self._check_new(url)
        assert fetched.body is not None
        parsed = parse_feed(fetched.body)
        self._check_new(url)  # again: another request may have subscribed meanwhile
        feed_id = self.db.insert_feed(url, parsed.title, policy, backfill_n)
        # Not due: this request does the first poll, and the poller shouldn't race it.
        self.db.update_feed(feed_id, next_poll_at=self._next_poll(feed_id))
        async with self._lock(feed_id):
            self._apply(feed_id, fetched, parsed)
        # The show's own artwork now, for the Library; its episodes' while you look.
        if parsed.image:
            await self.images.ensure(parsed.image)
        task = asyncio.create_task(self._cache_images(feed_id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        feed = self.db.feed(feed_id)
        assert feed is not None
        log.info("subscribed to %s (%s)", parsed.title, url)
        return feed

    def _check_new(self, url: str) -> None:
        existing = self.db.feed_by_url(url)
        if existing is not None:
            title = existing["title"] or "that show"
            raise AlreadySubscribedError(f"You already subscribe to {title}.")

    def import_opml(self, data: bytes, policy: Policy = "auto", backfill_n: int = 0) -> Imported:
        """Add every feed in an OPML file. The poller fetches them shortly."""
        added = skipped = 0
        with self.db.transaction():
            for outline in parse_opml(data):
                if self.db.feed_by_url(outline.url) is not None:
                    skipped += 1
                    continue
                self.db.insert_feed(outline.url, outline.title, policy, backfill_n)
                added += 1
        self._wake.set()
        return Imported(added, skipped)

    def export_opml(self) -> bytes:
        return export_opml([Outline(f["url"], f["title"]) for f in self.db.feeds()])

    def process(self, episode_id: int, *, now: bool = False) -> None:
        """Queue an episode you picked (design.md §10 `process`)."""
        self.db.enqueue(episode_id, DOWNLOAD, Priority.NOW if now else Priority.NEW)

    # -- polling ---------------------------------------------------------------------------

    async def run(self) -> None:
        """Poll every feed that is due, forever."""
        while True:
            due = self.db.feeds_due(now())
            if due:
                await asyncio.gather(*(self._poll_limited(feed_id) for feed_id in due))
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), TICK_S)

    async def _poll_limited(self, feed_id: int) -> None:
        async with self._limit:
            await self.poll(feed_id)

    async def poll(self, feed_id: int) -> FeedRow | None:
        """Fetch one feed now. A failure is recorded on the feed, not raised."""
        async with self._lock(feed_id):
            feed = self.db.feed(feed_id)
            if feed is None:
                return None  # unsubscribed meanwhile
            try:
                fetched = await fetch_feed(
                    self.http, feed["url"], feed["etag"], feed["last_modified"]
                )
                parsed = None if fetched.body is None else parse_feed(fetched.body)
                if self.db.feed(feed_id) is None:
                    return None
                self._apply(feed_id, fetched, parsed)
            except FeedError as e:
                self._failed(feed_id, str(e))
            except Exception as e:
                log.exception("polling feed %d failed", feed_id)
                self._failed(feed_id, f"Unexpected error: {e}")
        await self._cache_images(feed_id)
        return self.db.feed(feed_id)

    def _failed(self, feed_id: int, error: str) -> None:
        log.warning("feed %d: %s", feed_id, error)
        self.db.update_feed(
            feed_id, error=error, last_polled_at=now(), next_poll_at=self._next_poll(feed_id)
        )

    def _apply(self, feed_id: int, fetched: Fetched, parsed: ParsedFeed | None) -> None:
        """Store a successful fetch: the feed's metadata, its episodes and what to queue."""
        feed = self.db.feed(feed_id)
        assert feed is not None
        polled_at = now()
        with self.db.transaction():
            if fetched.moved_to and self.db.feed_by_url(fetched.moved_to) is None:
                log.info("feed %d moved permanently to %s", feed_id, fetched.moved_to)
                self.db.update_feed(feed_id, url=fetched.moved_to)
            self.db.update_feed(
                feed_id,
                etag=fetched.etag,
                last_modified=fetched.last_modified,
                last_polled_at=polled_at,
                error=None,
            )
            if parsed is not None:
                self._store(feed, parsed)
        self.db.update_feed(feed_id, next_poll_at=self._next_poll(feed_id))

    def _store(self, feed: FeedRow, parsed: ParsedFeed) -> None:
        feed_id = feed["id"]
        self.db.update_feed(
            feed_id,
            title=parsed.title,
            author=parsed.author,
            description=parsed.description,
            link=parsed.link,
            image=parsed.image,
            language=parsed.language,
            podcast_guid=parsed.podcast_guid,
        )
        new = []
        for episode in parsed.episodes:
            id_, inserted = self.db.upsert_episode(feed_id, _fields(episode))
            if inserted:
                new.append((id_, episode))
        if feed["first_polled_at"] is None:
            for id_ in self.db.newest_episodes(feed_id, feed["backfill_n"]):
                self.db.enqueue(id_, DOWNLOAD, Priority.BACKFILL)
            self.db.update_feed(feed_id, first_polled_at=now())
            return
        if new:
            log.info("feed %d (%s): %d new episodes", feed_id, parsed.title, len(new))
        if feed["policy"] != "auto":
            return
        oldest = (datetime.now(UTC) - NEW_EPISODE_MAX_AGE).isoformat(timespec="seconds")
        for id_, episode in new:
            if episode.published_at is None or episode.published_at >= oldest:
                self.db.enqueue(id_, DOWNLOAD, Priority.NEW)

    def _next_poll(self, feed_id: int) -> str:
        feed = self.db.feed(feed_id)
        assert feed is not None
        if feed["poll_interval_min"]:
            interval = timedelta(minutes=feed["poll_interval_min"])
        else:
            newest = self.db.newest_published_at(feed_id)
            dormant = newest is not None and datetime.fromisoformat(newest) < (
                datetime.now(UTC) - DORMANT_AFTER
            )
            interval = DORMANT_INTERVAL if dormant else DEFAULT_INTERVAL
        interval *= self.rng.uniform(1 - JITTER, 1 + JITTER)
        return (datetime.now(UTC) + interval).isoformat(timespec="milliseconds")

    async def _cache_images(self, feed_id: int) -> None:
        feed = self.db.feed(feed_id)
        if feed is None:
            return
        urls = [feed["image"]] if feed["image"] else []
        urls += [u for u in self.db.episode_images(feed_id, EPISODE_IMAGES) if u not in urls]
        try:
            for url in urls:
                await self.images.ensure(url)
        except Exception:
            # Artwork is decoration: never let it fail a poll or go unlogged in a task.
            log.exception("caching artwork for feed %d failed", feed_id)

    def _lock(self, feed_id: int) -> asyncio.Lock:
        return self._locks.setdefault(feed_id, asyncio.Lock())


def _newest_first(episodes: list[ParsedEpisode]) -> list[ParsedEpisode]:
    # Feeds are usually newest first already; undated items keep their place at the end.
    return sorted(episodes, key=lambda e: e.published_at or "", reverse=True)


def _fields(episode: ParsedEpisode) -> EpisodeFields:
    return EpisodeFields(
        guid=episode.guid,
        title=episode.title,
        description=episode.description,
        link=episode.link,
        published_at=episode.published_at,
        enclosure_url=episode.enclosure_url,
        enclosure_type=episode.enclosure_type,
        enclosure_length=episode.enclosure_length,
        duration_s=episode.duration_s,
        image=episode.image,
        chapters_url=episode.chapters_url,
        feed_transcript_url=episode.transcript_url,
        feed_transcript_type=episode.transcript_type,
        persons=[PersonRow(name=p.name, role=p.role, href=p.href) for p in episode.persons],
    )
