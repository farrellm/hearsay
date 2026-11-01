import asyncio
import io
import shutil
import subprocess
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from hearsay.app import create_app
from hearsay.config import Config
from hearsay.services import Services, build_services

Route = Callable[[httpx2.Request], httpx2.Response]


@dataclass
class Audio:
    """An audio file served with Range support, like a podcast CDN. With `hold_after`, the
    first response stops after that many bytes until `release` is set, so a test can
    interrupt a download midway."""

    data: bytes
    etag: str | None = '"a1"'
    ranges: bool = True
    hold_after: int | None = None
    held: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    sent: int = 0  # body bytes served, over every request

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        start = 0
        range_ = request.headers.get("range")
        if_range = request.headers.get("if-range")
        if self.ranges and range_ and (if_range is None or if_range == self.etag):
            start = int(range_.removeprefix("bytes=").removesuffix("-"))
            if start >= len(self.data):
                return httpx2.Response(416, headers={"content-range": f"bytes */{len(self.data)}"})
        headers = {
            "content-type": "audio/mpeg",
            "content-length": str(len(self.data) - start),
            "accept-ranges": "bytes",
        }
        if self.etag:
            headers["etag"] = self.etag
        if start:
            headers["content-range"] = f"bytes {start}-{len(self.data) - 1}/{len(self.data)}"
        return httpx2.Response(206 if start else 200, headers=headers, content=self._body(start))

    async def _body(self, start: int) -> AsyncIterator[bytes]:
        pos = start
        while pos < len(self.data):
            if self.hold_after is not None and pos >= self.hold_after and not self.release.is_set():
                self.held.set()
                await self.release.wait()
            chunk = self.data[pos : pos + 1024]
            pos += len(chunk)
            self.sent += len(chunk)
            yield chunk


@dataclass
class FakeWeb:
    """The internet as the server sees it: podcast hosts and their artwork, by URL."""

    routes: dict[str, Route] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)

    def feed(self, url: str, body: str | bytes, etag: str | None = None) -> None:
        """Serve `body` at `url`, answering 304 to a matching If-None-Match."""
        data = body.encode() if isinstance(body, str) else body

        def route(request: httpx2.Request) -> httpx2.Response:
            if etag is not None and request.headers.get("if-none-match") == etag:
                return httpx2.Response(304, headers={"etag": etag})
            headers = {"content-type": "application/rss+xml"}
            if etag is not None:
                headers["etag"] = etag
            return httpx2.Response(200, content=data, headers=headers)

        self.routes[url] = route

    def redirect(self, url: str, to: str, status: int = 301) -> None:
        self.routes[url] = lambda _r: httpx2.Response(status, headers={"location": to})

    def image(self, url: str, color: str = "red", size: tuple[int, int] = (600, 600)) -> None:
        buf = io.BytesIO()
        Image.new("RGB", size, color).save(buf, "PNG")
        data = buf.getvalue()
        self.routes[url] = lambda _r: httpx2.Response(
            200, content=data, headers={"content-type": "image/png"}
        )

    def audio(self, url: str, route: Audio) -> Audio:
        self.routes[url] = route
        return route

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        url = str(request.url)
        if url not in self.routes:
            return httpx2.Response(404, text="not found")
        return self.routes[url](request)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.MockTransport(self.handler), follow_redirects=True
        )

    def fetched(self, url: str) -> int:
        return sum(1 for r in self.requests if str(r.url) == url)


def _encode(path: Path, *args: str) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            *args,
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


@pytest.fixture(scope="session")
def mp3(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """3 s of tone as an MP3 (ffmpeg is a server dependency, and CI installs it)."""
    if shutil.which("ffmpeg") is None:
        pytest.fail("ffmpeg is required for the download tests")
    return _encode(tmp_path_factory.mktemp("audio") / "tone.mp3", "-c:a", "libmp3lame")


@pytest.fixture(scope="session")
def m4a(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return _encode(tmp_path_factory.mktemp("audio") / "tone.m4a", "-c:a", "aac")


@pytest.fixture
def web_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Hearsay</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log('hi')")
    (dist / "sw.js").write_text("// sw")
    return dist


@pytest.fixture
def config(tmp_path: Path, web_dist: Path) -> Config:
    return Config(data_dir=tmp_path / "data", web_dist=web_dist)


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def client(config: Config, web: FakeWeb) -> Iterator[TestClient]:
    def services(c: Config) -> Services:
        return build_services(c, web.client(), background=False)

    with TestClient(create_app(config, services)) as c:
        yield c


def svc(client: TestClient) -> Services:
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services
