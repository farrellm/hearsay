"""Health."""

from typing import Any

from fastapi import APIRouter

from hearsay import __version__
from hearsay.api.deps import Svc

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(svc: Svc) -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "schema": svc.db.schema_version}
