"""Fetching feeds over HTTP, with conditional GET (design.md §4)."""

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx2

from hearsay import __version__
from hearsay.feeds.parser import FeedError

USER_AGENT = f"Hearsay/{__version__} (personal podcast app)"
MAX_FEED_BYTES = 50 * 2**20  # long-running shows publish multi-megabyte feeds
PERMANENT_REDIRECTS = (301, 308)


def http_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        follow_redirects=True,
        timeout=httpx2.Timeout(30, read=60),
        headers={"User-Agent": USER_AGENT},
    )


def check_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise FeedError("That isn't a web link. A feed's link starts with https://.")
    return url


@dataclass
class Fetched:
    body: bytes | None  # None: not modified since the given validators
    etag: str | None
    last_modified: str | None
    moved_to: str | None  # the new URL, when every redirect on the way was permanent


async def fetch_feed(
    http: httpx2.AsyncClient,
    url: str,
    etag: str | None = None,
    last_modified: str | None = None,
) -> Fetched:
    headers = {"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    host = urlsplit(url).netloc
    try:
        async with http.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 304:
                return Fetched(None, etag, last_modified, _moved_to(resp, url))
            if resp.status_code >= 400:
                raise FeedError(f"{host} answered HTTP {resp.status_code} for the feed.")
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > MAX_FEED_BYTES:
                    raise FeedError(f"The feed at {host} is over {MAX_FEED_BYTES >> 20} MB.")
            return Fetched(
                bytes(body),
                resp.headers.get("etag"),
                resp.headers.get("last-modified"),
                _moved_to(resp, url),
            )
    except httpx2.TooManyRedirects:
        raise FeedError(f"{host} redirects the feed in a loop.") from None
    except httpx2.HTTPError as e:
        raise FeedError(f"Couldn't reach {host}: {str(e) or type(e).__name__}") from None


def _moved_to(resp: httpx2.Response, url: str) -> str | None:
    if not resp.history or any(r.status_code not in PERMANENT_REDIRECTS for r in resp.history):
        return None
    final = str(resp.url)
    return final if final != url else None
