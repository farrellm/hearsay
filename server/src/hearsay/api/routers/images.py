"""Cached artwork (design.md §4)."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from hearsay.api.deps import Svc

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/{name}")
async def image(name: str, svc: Svc) -> FileResponse:
    """`<sha256>-<512|128>.webp`. Named by content, so it never changes."""
    path = svc.images.path(name)
    if path is None:
        raise HTTPException(404, "No such image")
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
