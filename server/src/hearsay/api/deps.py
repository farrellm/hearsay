"""What routes share: the services, and lookups that answer 404 for what isn't there.

Routes that return a stored row are annotated `Mapping[str, Any]`, not the row's TypedDict:
FastAPI would take the TypedDict for a response model and drop whatever a route adds to the
row.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from hearsay.db import FeedRow
from hearsay.services import Services


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


Svc = Annotated[Services, Depends(services)]


def feed_or_404(svc: Services, feed_id: int) -> FeedRow:
    feed = svc.db.feed(feed_id)
    if feed is None:
        raise HTTPException(404, "Unknown show")
    return feed


def episode_or_404(svc: Services, episode_id: int) -> None:
    if not svc.db.episode_exists(episode_id):
        raise HTTPException(404, "Unknown episode")
