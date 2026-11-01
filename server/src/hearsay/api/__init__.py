"""REST API under /api (design.md §10)."""

from fastapi import APIRouter

from hearsay.api.errors import register_error_handlers
from hearsay.api.routers import episodes, feeds, images, system

router = APIRouter(prefix="/api")
for _module in (system, feeds, episodes, images):
    router.include_router(_module.router)

__all__ = ["register_error_handlers", "router"]
