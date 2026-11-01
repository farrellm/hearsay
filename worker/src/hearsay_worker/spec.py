"""Types shared by the server and the worker (design.md §5.1).

The worker writes a `Transcript`; the server stores it compressed and indexes its segments.
Field names are short because an hour of audio has about 10k words.
"""

from pydantic import BaseModel, ConfigDict, Field


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Word(Spec):
    w: str
    s: float = Field(ge=0)  # start, seconds
    e: float = Field(ge=0)  # end, seconds
    p: float | None = Field(default=None, ge=0, le=1)  # ASR probability


class Segment(Spec):
    i: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker: str | None = None  # "S0", "S1", …; None when diarization is off
    text: str
    words: list[Word] = []


class Engine(Spec):
    asr: str
    align: str | None = None
    diarize: str | None = None


class Transcript(Spec):
    engine: Engine
    language: str
    duration_s: float = Field(ge=0)
    speakers: list[str] = []
    segments: list[Segment]
