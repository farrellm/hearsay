"""ffmpeg and ffprobe, run as subprocesses (design.md §3)."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from hearsay.errors import HearsayError

NOT_AUDIO = "The file isn't audio."


class MediaError(HearsayError):
    """A file ffmpeg can't use (permanent), or ffmpeg not running (worth trying again)."""

    def __init__(self, message: str, *, permanent: bool = True) -> None:
        super().__init__(message)
        self.permanent = permanent


class _ExitError(MediaError):
    """The tool ran and rejected its input."""


@dataclass
class AudioInfo:
    duration_s: float
    format: str  # ffprobe's format_name, e.g. "mp3" or "mov,mp4,m4a,3gp,3g2,mj2"
    codec: str  # the first audio stream's codec, e.g. "mp3" or "aac"


async def _run(*args: str, timeout: float = 120) -> bytes:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise MediaError(f"{args[0]} is not installed on the server.", permanent=False) from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise MediaError(f"{args[0]} timed out.", permanent=False) from None
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip().splitlines()[-3:]
        raise _ExitError(f"{args[0]} failed: {' | '.join(tail)}")
    return out


async def probe_audio(path: Path) -> AudioInfo:
    """The duration and format of an audio file. Raises MediaError if it isn't one."""
    try:
        out = await _run(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name:format=duration,format_name",
            "-of",
            "json",
            str(path),
        )
    except _ExitError:
        raise MediaError(NOT_AUDIO) from None
    info = json.loads(out)
    audio = [s for s in info.get("streams") or [] if s.get("codec_type") == "audio"]
    fmt = info.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0
    if not audio or duration <= 0:
        raise MediaError(NOT_AUDIO)
    return AudioInfo(
        duration_s=round(duration, 3),
        format=str(fmt.get("format_name", "")),
        codec=str(audio[0].get("codec_name", "")),
    )
