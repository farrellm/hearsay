import io
from collections.abc import Callable, Iterator
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
        return build_services(c, web.client(), poll=False)

    with TestClient(create_app(config, services)) as c:
        yield c


def svc(client: TestClient) -> Services:
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services
