"""Downloading episodes' audio (design.md §3 step 2) into the content-addressed store (§7.1).

A download writes to `audio/.partial/<episode>.part`, with a sidecar `.json` holding the
enclosure URL and the response's validators. If it is interrupted (a dropped connection, or
the server stopping), the next attempt asks for the rest with `Range`, and with `If-Range`
when the host gave a validator, so the bytes are only joined when they are the same file.
Hosts that insert ads dynamically serve different bytes per request; a resumed download is
checked against the length the first response announced, and starts over on any mismatch.
"""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2

from hearsay.errors import HearsayError

MAX_AUDIO_BYTES = 2 << 30  # a long episode is a few hundred MB; video isn't wanted (§1)
# Answers that won't change if asked again; anything else is retried later.
PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 405, 410, 451})
CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")

# ffprobe's codec → the stored file's extension and the type it's served with.
FORMATS: dict[str, tuple[str, str]] = {
    "mp3": ("mp3", "audio/mpeg"),
    "aac": ("m4a", "audio/mp4"),
    "alac": ("m4a", "audio/mp4"),
    "opus": ("opus", "audio/ogg"),
    "vorbis": ("ogg", "audio/ogg"),
    "flac": ("flac", "audio/flac"),
}
RAW_AAC = ("aac", "audio/aac")  # ADTS, not in an MP4 container
UNKNOWN = ("audio", "application/octet-stream")


class DownloadError(HearsayError):
    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass
class Fetched:
    sha256: str
    size: int
    url: str  # where the bytes finally came from, after redirects


def audio_format(codec: str, container: str) -> tuple[str, str]:
    """The extension and media type for audio ffprobe described."""
    if codec == "aac" and container == "aac":
        return RAW_AAC
    if codec.startswith("pcm_"):
        return ("wav", "audio/wav")
    return FORMATS.get(codec, UNKNOWN)


class AudioStore:
    """`audio/ab/abcdef….mp3`: originals named by the sha256 of their bytes."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.root = data_dir / "audio"
        self.partials = self.root / ".partial"

    def partial(self, episode_id: int) -> Path:
        self.partials.mkdir(parents=True, exist_ok=True)
        return self.partials / f"{episode_id}.part"

    def discard_partial(self, episode_id: int) -> None:
        part = self.partials / f"{episode_id}.part"
        part.unlink(missing_ok=True)
        _meta_path(part).unlink(missing_ok=True)

    def add(self, part: Path, sha256: str, ext: str) -> str:
        """Move a finished download into the store. Returns its path, relative to the data
        directory. A file with the same bytes already there is kept, and `part` dropped."""
        final = self.root / sha256[:2] / f"{sha256}.{ext}"
        if final.is_file():
            part.unlink()
        else:
            final.parent.mkdir(parents=True, exist_ok=True)
            part.replace(final)
        return final.relative_to(self.data_dir).as_posix()

    def path(self, relative: str) -> Path:
        return self.data_dir / relative

    def remove(self, relative: str) -> None:
        (self.data_dir / relative).unlink(missing_ok=True)


async def fetch_audio(
    http: httpx2.AsyncClient, url: str, part: Path, max_bytes: int = MAX_AUDIO_BYTES
) -> Fetched:
    """Download `url` into `part`, resuming what an earlier attempt left there."""
    host = urlsplit(url).netloc
    meta, have = await asyncio.to_thread(_resume_point, part, url)
    if meta is not None and have and have == meta["total"]:
        # Interrupted after the last byte, before the file was stored.
        return Fetched(await asyncio.to_thread(_hash_file, part), have, meta["final"])
    try:
        for _ in range(2):  # a refused resume starts over, once
            fetched = await _attempt(http, url, part, (meta, have), max_bytes)
            if fetched is not None:
                _meta_path(part).unlink(missing_ok=True)
                return fetched
            _discard(part)
            meta, have = None, 0
    except httpx2.HTTPError as e:
        msg = f"Couldn't download from {host}: {str(e) or type(e).__name__}"
        raise DownloadError(msg) from None
    except DownloadError as e:
        if e.permanent:
            _discard(part)
        raise
    raise DownloadError(f"{host} refused to resume the download.")


async def _attempt(
    http: httpx2.AsyncClient,
    url: str,
    part: Path,
    resume: tuple[dict[str, Any] | None, int],
    max_bytes: int,
) -> Fetched | None:
    """One request: the rest of the file if `have` bytes are in `part`, else all of it.
    None if the host won't continue the partial file, which must then be started over."""
    host = urlsplit(url).netloc
    meta, have = resume
    headers = {}
    if have and meta is not None:
        headers["Range"] = f"bytes={have}-"
        if validator := _validator(meta):
            headers["If-Range"] = validator
    async with http.stream("GET", url, headers=headers) as resp:
        if resp.status_code == 416 and have:
            return None
        if resp.status_code >= 400:
            raise DownloadError(
                f"{host} answered HTTP {resp.status_code} for the audio.",
                permanent=resp.status_code in PERMANENT_STATUSES,
            )
        if have and meta is not None and resp.status_code == 206:
            if not _continues(resp, have, meta["total"]):
                return None
            hasher = await asyncio.to_thread(_hasher_of, part)
            total: int | None = meta["total"]
        else:  # a fresh start: a new file, or a host that ignored the Range
            have, total, hasher = 0, _total(resp), hashlib.sha256()
            meta = {
                "url": url,
                "final": str(resp.url),
                "etag": resp.headers.get("etag"),
                "last_modified": resp.headers.get("last-modified"),
                "total": total,
            }
            await asyncio.to_thread(_meta_path(part).write_text, json.dumps(meta))
        too_big = DownloadError(f"The audio is over {max_bytes >> 20} MB.", permanent=True)
        if total is not None and total > max_bytes:
            raise too_big
        size = have
        # Small sequential writes; a thread hop per chunk would cost more than it saves.
        with part.open("ab" if have else "wb") as f:
            async for chunk in resp.aiter_bytes():
                f.write(chunk)
                hasher.update(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise too_big
        if total is not None and size != total:
            raise DownloadError(f"{host} ended the download early.")  # kept, to resume
        return Fetched(hasher.hexdigest(), size, str(resp.url))


def _resume_point(part: Path, url: str) -> tuple[dict[str, Any] | None, int]:
    """What an earlier attempt left: its first response's metadata and the bytes it got."""
    meta = _load_meta(part, url)
    if meta is None:
        _discard(part)
        return None, 0
    return meta, part.stat().st_size if part.exists() else 0


def _meta_path(part: Path) -> Path:
    return part.with_suffix(".json")


def _discard(part: Path) -> None:
    part.unlink(missing_ok=True)
    _meta_path(part).unlink(missing_ok=True)


def _load_meta(part: Path, url: str) -> dict[str, Any] | None:
    """What the response that started `part` said, if `part` is a download of `url`."""
    try:
        meta: dict[str, Any] = json.loads(_meta_path(part).read_text())
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and meta.get("url") == url else None


def _validator(meta: dict[str, Any]) -> str | None:
    etag = meta.get("etag")
    if isinstance(etag, str) and not etag.startswith("W/"):  # If-Range needs a strong one
        return etag
    last_modified = meta.get("last_modified")
    return last_modified if isinstance(last_modified, str) else None


def _total(resp: httpx2.Response) -> int | None:
    if "content-encoding" in resp.headers:
        return None  # the length is of the encoded body
    length = resp.headers.get("content-length")
    return int(length) if length and length.isdigit() else None


def _continues(resp: httpx2.Response, have: int, total: int | None) -> bool:
    """Whether a 206 is the rest of the file the partial download started."""
    match = CONTENT_RANGE.fullmatch(resp.headers.get("content-range", "").strip())
    if match is None or int(match[1]) != have:
        return False
    return total is None or match[3] == str(total)


def _hasher_of(path: Path) -> "hashlib._Hash":
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            hasher.update(block)
    return hasher


def _hash_file(path: Path) -> str:
    return _hasher_of(path).hexdigest()
