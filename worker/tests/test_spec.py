import pytest
from pydantic import ValidationError

from hearsay_worker.spec import Transcript

EXAMPLE = {
    "engine": {
        "asr": "faster-whisper/large-v3-turbo",
        "align": "torchaudio/MMS_FA",
        "diarize": "pyannote/community-1",
    },
    "language": "en",
    "duration_s": 3712.4,
    "speakers": ["S0", "S1"],
    "segments": [
        {
            "i": 0,
            "start": 0.42,
            "end": 6.10,
            "speaker": "S0",
            "text": "Welcome back to the show.",
            "words": [{"w": "Welcome", "s": 0.42, "e": 0.80, "p": 0.98}],
        }
    ],
}


def test_design_doc_example_round_trips() -> None:
    transcript = Transcript.model_validate(EXAMPLE)
    assert transcript.segments[0].words[0].w == "Welcome"
    assert Transcript.model_validate_json(transcript.model_dump_json()) == transcript


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Transcript.model_validate({**EXAMPLE, "extra": 1})
