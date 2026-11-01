"""The artwork cache (design.md §4, §7.1): remote images, fetched once and stored as
`images/<sha256>-{512,128}.webp`, where the hash is of the downloaded file."""

import asyncio
import hashlib
import io
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
from PIL import Image, ImageOps, UnidentifiedImageError

from hearsay.db import Database, now

log = logging.getLogger(__name__)

SIZES = (512, 128)
MAX_IMAGE_BYTES = 20 * 2**20
RETRY_AFTER = timedelta(days=1)  # a failed fetch is tried again after this
NAME = re.compile(r"^([0-9a-f]{64})-(512|128)\.webp$")


class ImageCache:
    def __init__(self, db: Database, root: Path, http: httpx2.AsyncClient) -> None:
        self.db = db
        self.root = root
        self.http = http

    def path(self, name: str) -> Path | None:
        """The cached file named `<sha256>-<size>.webp`, if it exists."""
        match = NAME.match(name)
        if match is None:
            return None
        path = self.root / match[1][:2] / name
        return path if path.is_file() else None

    async def ensure(self, url: str) -> str | None:
        """Cache `url` unless it is cached (or failed recently). Returns the image's hash."""
        row = self.db.image(url)
        if row is not None:
            retry = datetime.fromisoformat(row["fetched_at"]) + RETRY_AFTER
            if row["sha256"] is not None or datetime.now(UTC) < retry:
                return row["sha256"]
        try:
            data = await self._download(url)
            sha = hashlib.sha256(data).hexdigest()
            await asyncio.to_thread(self._write, sha, data)
        except (httpx2.HTTPError, OSError, ValueError, UnidentifiedImageError) as e:
            log.info("couldn't cache image %s: %s", url, e)
            self.db.save_image(url, None, now(), str(e) or type(e).__name__)
            return None
        self.db.save_image(url, sha, now(), None)
        return sha

    async def _download(self, url: str) -> bytes:
        async with self.http.stream("GET", url) as resp:
            resp.raise_for_status()
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("image too large")
            return bytes(data)

    def _write(self, sha: str, data: bytes) -> None:
        folder = self.root / sha[:2]
        if all((folder / f"{sha}-{size}.webp").is_file() for size in SIZES):
            return
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            image = ImageOps.exif_transpose(img)
        image = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
        folder.mkdir(parents=True, exist_ok=True)
        for size in SIZES:
            copy = image.copy()
            copy.thumbnail((size, size), Image.Resampling.LANCZOS)
            tmp = folder / f".{sha}-{size}.webp.tmp"
            copy.save(tmp, "WEBP", quality=82)
            tmp.replace(folder / f"{sha}-{size}.webp")
