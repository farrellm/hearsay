"""Request bodies."""

from pydantic import BaseModel, ConfigDict, Field

from hearsay.db import Policy


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedLink(Body):
    url: str = Field(min_length=1, max_length=2048)


class Subscribe(FeedLink):
    policy: Policy = "auto"
    backfill_n: int = Field(default=10, ge=0, le=10_000)


class FeedPatch(Body):
    policy: Policy | None = None
    diarize: bool | None = None


class Process(Body):
    now: bool = False  # Transcribe now: ahead of everything else
