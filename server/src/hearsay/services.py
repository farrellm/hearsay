"""Wiring of the server's long-lived components."""

import asyncio
import contextlib
from dataclasses import dataclass, field

import httpx2

from hearsay.config import Config
from hearsay.db import Database
from hearsay.feeds.fetch import http_client
from hearsay.feeds.service import Feeds
from hearsay.media.images import ImageCache


@dataclass
class Services:
    config: Config
    db: Database
    http: httpx2.AsyncClient
    images: ImageCache
    feeds: Feeds
    poll: bool = True  # run the feed poller (tests drive polls themselves)
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def start(self) -> None:
        if self.poll:
            self._tasks.append(asyncio.create_task(self.feeds.run()))

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
    config: Config, http: httpx2.AsyncClient | None = None, *, poll: bool = True
) -> Services:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    http = http or http_client()
    images = ImageCache(db, config.data_dir / "images", http)
    return Services(
        config=config,
        db=db,
        http=http,
        images=images,
        feeds=Feeds(db, http, images),
        poll=poll,
    )
