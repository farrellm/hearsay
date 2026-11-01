"""Episodes (design.md §10)."""

from fastapi import APIRouter

from hearsay.api.deps import Svc, episode_or_404
from hearsay.api.schemas import Process

router = APIRouter(prefix="/episodes", tags=["episodes"])


@router.post("/{episode_id}/process", status_code=204)
async def process(episode_id: int, svc: Svc, body: Process | None = None) -> None:
    """Queue the episode for download and transcription."""
    episode_or_404(svc, episode_id)
    svc.feeds.process(episode_id, now=body.now if body else False)
