"""Wiring of the server's long-lived components."""

import asyncio
import contextlib
from dataclasses import dataclass, field

import httpx2

from hearsay.config import Config
from hearsay.db import Database
from hearsay.feeds.fetch import http_client
from hearsay.feeds.service import Feeds
from hearsay.media.download import AudioStore
from hearsay.media.images import ImageCache
from hearsay.tasks import Downloads


@dataclass
class Services:
    config: Config
    db: Database
    http: httpx2.AsyncClient
    images: ImageCache
    feeds: Feeds
    audio: AudioStore
    downloads: Downloads
    background: bool = True  # run the poller and the downloads (tests drive them themselves)
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def start(self) -> None:
        if self.background:
            self._tasks.append(asyncio.create_task(self.feeds.run()))
            self._tasks.append(asyncio.create_task(self.downloads.run()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        await self.feeds.aclose()
        await self.http.aclose()
        self.db.close()


def build_services(
    config: Config, http: httpx2.AsyncClient | None = None, *, background: bool = True
) -> Services:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    http = http or http_client()
    images = ImageCache(db, config.data_dir / "images", http)
    audio = AudioStore(config.data_dir)
    downloads = Downloads(db, http, audio)
    return Services(
        config=config,
        db=db,
        http=http,
        images=images,
        feeds=Feeds(db, http, images, queued=downloads.wake),
        audio=audio,
        downloads=downloads,
        background=background,
    )
