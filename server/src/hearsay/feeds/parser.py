"""RSS parsing (design.md §4): feedparser for the feed itself, plus a pass over the
Podcasting 2.0 elements it keeps only one of (`podcast:transcript`, `podcast:person`)."""

import calendar
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from xml.etree.ElementTree import Element

import feedparser
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from hearsay.errors import HearsayError

PODCAST_NS = "https://podcastindex.org/namespace/1.0"
# Older feeds still use the namespace's first URL.
PODCAST_NAMESPACES = (
    PODCAST_NS,
    "https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md",
)

# Transcript formats, best first: ones with timings can stand in for ASR (§3), HTML can't.
TRANSCRIPT_TYPES = (
    "application/json",
    "text/vtt",
    "application/x-subrip",
    "application/srt",
    "text/srt",
    "text/html",
    "text/plain",
)


class FeedError(HearsayError):
    """A feed that can't be fetched or read."""


@dataclass
class Person:
    name: str
    role: str | None = None
    href: str | None = None


@dataclass
class ParsedEpisode:
    guid: str
    title: str | None
    description: str | None  # HTML, sanitized by feedparser
    link: str | None
    published_at: str | None  # ISO 8601 UTC
    enclosure_url: str
    enclosure_type: str | None
    enclosure_length: int | None
    duration_s: float | None
    image: str | None
    chapters_url: str | None = None
    transcript_url: str | None = None
    transcript_type: str | None = None
    persons: list[Person] = field(default_factory=list)


@dataclass
class ParsedFeed:
    title: str
    author: str | None
    description: str | None
    link: str | None
    image: str | None
    language: str | None
    podcast_guid: str | None
    episodes: list[ParsedEpisode]  # in feed order


def parse_feed(data: bytes) -> ParsedFeed:
    """Parse a fetched feed. Raises `FeedError` when it isn't a podcast feed."""
    parsed = feedparser.parse(data, sanitize_html=True, resolve_relative_uris=True)
    channel: Any = parsed.feed
    if not parsed.version or not channel.get("title"):
        msg = "That link isn't an RSS feed. Check that it's the show's feed, not its web page."
        raise FeedError(msg)
    extras = _podcast_extras(data)
    episodes = []
    for entry in parsed.entries:
        episode = _episode(entry)
        if episode is None:
            continue
        extra = extras.get(episode.guid) or extras.get(episode.enclosure_url)
        if extra is not None:
            episode.persons = extra.persons
            if extra.transcript_url:
                episode.transcript_url = extra.transcript_url
                episode.transcript_type = extra.transcript_type
        episodes.append(episode)
    return ParsedFeed(
        title=_text(channel.get("title")) or "Untitled",
        author=_text(channel.get("author") or channel.get("itunes_author")),
        description=channel.get("summary") or None,
        link=channel.get("link") or None,
        image=_image(channel),
        language=_text(channel.get("language")),
        podcast_guid=_text(channel.get("podcast_guid")),
        episodes=episodes,
    )


def _episode(entry: Any) -> ParsedEpisode | None:
    enclosure = _enclosure(entry)
    if enclosure is None:
        return None  # a text post: nothing to play
    url = enclosure["href"]
    guid = _text(entry.get("id")) or url
    content = entry.get("content") or []
    summary = entry.get("summary") or ""
    # content:encoded is usually the full show notes; the description often a teaser.
    longest = max([summary, *(c.get("value", "") for c in content)], key=len)
    transcript = entry.get("podcast_transcript") or {}
    chapters = entry.get("podcast_chapters") or {}
    return ParsedEpisode(
        guid=guid,
        title=_text(entry.get("title")),
        description=longest or None,
        link=entry.get("link") or None,
        published_at=_iso(entry.get("published_parsed") or entry.get("updated_parsed")),
        enclosure_url=url,
        enclosure_type=enclosure.get("type") or None,
        enclosure_length=_int(enclosure.get("length")),
        duration_s=parse_duration(entry.get("itunes_duration")),
        image=_image(entry),
        chapters_url=chapters.get("url") or None,
        transcript_url=transcript.get("url") or None,
        transcript_type=transcript.get("type") or None,
    )


def _enclosure(entry: Any) -> dict[str, Any] | None:
    enclosures = [link for link in entry.get("links", []) if link.get("rel") == "enclosure"]
    enclosures = [e for e in enclosures if e.get("href")]
    for e in enclosures:
        if str(e.get("type", "")).startswith("audio/"):
            return dict(e)
    # Untyped or mistyped enclosures are common; video ones are played for their audio.
    return dict(enclosures[0]) if enclosures else None


def _image(node: Any) -> str | None:
    image = node.get("image") or {}
    href = image.get("href") or image.get("url")
    return str(href) if href else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    try:
        n = int(str(value).strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _iso(parsed: time.struct_time | None) -> str | None:
    if parsed is None:
        return None
    # feedparser normalizes dates to UTC struct_times.
    stamp = datetime.fromtimestamp(calendar.timegm(parsed), UTC)
    return stamp.isoformat(timespec="seconds")


_DURATION = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")


def parse_duration(value: Any) -> float | None:
    """`itunes:duration`: seconds, `MM:SS` or `HH:MM:SS`."""
    if value is None:
        return None
    match = _DURATION.match(str(value).strip())
    if match is None:
        return None
    parts = [p for p in match.groups() if p is not None]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds if seconds > 0 else None


@dataclass
class _Extra:
    persons: list[Person]
    transcript_url: str | None
    transcript_type: str | None


def _podcast_extras(data: bytes) -> dict[str, _Extra]:
    """Each item's people and best transcript, by guid and by enclosure URL."""
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, DefusedXmlException):
        return {}  # feedparser read it leniently; go without the extras
    extras: dict[str, _Extra] = {}
    for item in root.iter("item"):
        extra = _Extra(_persons(item), *_best_transcript(item))
        guid = _text(item.findtext("guid"))
        enclosure = item.find("enclosure")
        url = enclosure.get("url") if enclosure is not None else None
        for key in (guid, url):
            if key:
                extras[key] = extra
    return extras


def _podcast_children(item: Element, name: str) -> list[Element]:
    return [el for ns in PODCAST_NAMESPACES for el in item.findall(f"{{{ns}}}{name}")]


def _persons(item: Element) -> list[Person]:
    people = []
    for el in _podcast_children(item, "person"):
        name = _text(el.text)
        if name:
            people.append(Person(name, el.get("role", "host").lower(), el.get("href")))
    return people


def _best_transcript(item: Element) -> tuple[str | None, str | None]:
    candidates = [
        (el.get("url"), (el.get("type") or "").lower())
        for el in _podcast_children(item, "transcript")
        if el.get("url")
    ]
    if not candidates:
        return None, None

    def rank(candidate: tuple[str | None, str]) -> int:
        kind = candidate[1]
        return TRANSCRIPT_TYPES.index(kind) if kind in TRANSCRIPT_TYPES else len(TRANSCRIPT_TYPES)

    url, kind = min(candidates, key=rank)
    return url, kind or None
