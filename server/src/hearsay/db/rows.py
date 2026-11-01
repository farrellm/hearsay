"""The rows the metadata store returns: plain dicts, typed.

They are what the API sends, so a route may add to one, but nothing here is renamed on the
way out.
"""

from typing import Literal, TypedDict

Policy = Literal["auto", "manual"]
TaskStatus = Literal["pending", "running", "done", "error"]


class PersonRow(TypedDict):
    name: str
    role: str | None
    href: str | None


class FeedRow(TypedDict):
    id: int
    url: str
    title: str | None
    author: str | None
    description: str | None
    link: str | None
    image: str | None  # the remote URL; the cached copy is `artwork` in summaries
    language: str | None
    etag: str | None
    last_modified: str | None
    last_polled_at: str | None
    next_poll_at: str | None
    first_polled_at: str | None
    poll_interval_min: int | None
    policy: Policy
    backfill_n: int
    diarize: bool
    language_override: str | None
    podcast_guid: str | None
    default_speed: float | None
    audio_keep_days: int | None
    error: str | None
    created_at: str


class FeedUpdate(TypedDict, total=False):
    url: str
    title: str | None
    author: str | None
    description: str | None
    link: str | None
    image: str | None
    language: str | None
    etag: str | None
    last_modified: str | None
    last_polled_at: str | None
    next_poll_at: str | None
    first_polled_at: str | None
    policy: Policy
    diarize: bool
    podcast_guid: str | None
    error: str | None


class FeedSummary(TypedDict):
    """A show in the Library list."""

    id: int
    url: str
    title: str | None
    author: str | None
    artwork: str | None  # cached image hash
    policy: Policy
    error: str | None
    episode_count: int
    unplayed: int
    newest_title: str | None
    newest_published_at: str | None


class EpisodeFields(TypedDict):
    """What a feed says about an episode."""

    guid: str
    title: str | None
    description: str | None
    link: str | None
    published_at: str | None
    enclosure_url: str
    enclosure_type: str | None
    enclosure_length: int | None
    duration_s: float | None
    image: str | None
    chapters_url: str | None
    feed_transcript_url: str | None
    feed_transcript_type: str | None
    persons: list[PersonRow]


class EpisodeRow(TypedDict):
    """The parts of an episode the pipeline works from."""

    id: int
    feed_id: int
    title: str | None
    enclosure_url: str
    enclosure_type: str | None
    duration_s: float | None
    audio_sha256: str | None
    audio_path: str | None  # relative to the data directory
    audio_size: int | None
    audio_url: str | None
    audio_type: str | None
    audio_changes: int
    state: str
    error: str | None


class EpisodeUpdate(TypedDict, total=False):
    duration_s: float | None
    audio_sha256: str | None
    audio_path: str | None
    audio_size: int | None
    audio_url: str | None
    audio_type: str | None
    audio_changes: int
    state: str
    error: str | None


class EpisodeSummary(TypedDict):
    """An episode in a show's list."""

    id: int
    title: str | None
    published_at: str | None
    duration_s: float | None
    state: str
    error: str | None  # why its latest stage failed
    artwork: str | None
    task: TaskStatus | None  # the download task's status, if it has been queued
    finished: bool


class TaskRow(TypedDict):
    id: int
    episode_id: int
    stage: str
    status: TaskStatus
    priority: int
    attempts: int  # failed runs so far
    not_before: str | None  # a retry waits until then
    last_error: str | None


class ImageRow(TypedDict):
    url: str
    sha256: str | None
    fetched_at: str
    error: str | None
