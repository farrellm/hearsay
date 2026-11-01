"""Shows: subscribe, OPML, the Library list, and a show's episodes (design.md §10)."""

from collections.abc import Mapping
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from hearsay.api.deps import Svc, feed_or_404
from hearsay.api.schemas import FeedLink, FeedPatch, Subscribe
from hearsay.db import FeedSummary, Policy

router = APIRouter(prefix="/feeds", tags=["feeds"])

MAX_OPML_BYTES = 5 * 2**20


@router.get("")
async def list_feeds(svc: Svc) -> list[FeedSummary]:
    return svc.db.feeds()


@router.post("/preview")
async def preview(body: FeedLink, svc: Svc) -> dict[str, Any]:
    return asdict(await svc.feeds.preview(body.url))


@router.post("", status_code=201)
async def subscribe(body: Subscribe, svc: Svc) -> Mapping[str, Any]:
    return await svc.feeds.subscribe(body.url, body.policy, body.backfill_n)


@router.post("/import-opml")
async def import_opml(
    request: Request,
    svc: Svc,
    policy: Policy = "auto",
    backfill_n: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> dict[str, int]:
    """The OPML file is the request body, as it is."""
    data = await request.body()
    if len(data) > MAX_OPML_BYTES:
        raise HTTPException(413, "That OPML file is too big.")
    return asdict(svc.feeds.import_opml(data, policy, backfill_n))


@router.get("/export-opml")
async def export_opml(svc: Svc) -> Response:
    return Response(
        svc.feeds.export_opml(),
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="hearsay.opml"'},
    )


@router.get("/{feed_id}")
async def get_feed(feed_id: int, svc: Svc) -> dict[str, Any]:
    """The show and its episodes, newest first."""
    feed = feed_or_404(svc, feed_id)
    image = svc.db.image(feed["image"]) if feed["image"] else None
    return {
        "feed": {**feed, "artwork": image["sha256"] if image else None},
        "episodes": svc.db.feed_episodes(feed_id),
    }


@router.patch("/{feed_id}")
async def update_feed(feed_id: int, body: FeedPatch, svc: Svc) -> Mapping[str, Any]:
    feed_or_404(svc, feed_id)
    svc.db.update_feed(feed_id, **body.model_dump(exclude_none=True))
    return feed_or_404(svc, feed_id)


@router.delete("/{feed_id}", status_code=204)
async def unsubscribe(feed_id: int, svc: Svc) -> None:
    feed_or_404(svc, feed_id)
    svc.db.delete_feed(feed_id)


@router.post("/{feed_id}/refresh")
async def refresh(feed_id: int, svc: Svc) -> Mapping[str, Any]:
    """Poll now. A failure is the feed's `error`, as it would be from the poller."""
    feed_or_404(svc, feed_id)
    feed = await svc.feeds.poll(feed_id)
    if feed is None:
        raise HTTPException(404, "Unknown show")
    return feed
