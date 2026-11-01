"""FastAPI application: REST API and the static PWA."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from hearsay import __version__
from hearsay.api import register_error_handlers, router
from hearsay.config import Config, load_config
from hearsay.services import Services, build_services


class AppFiles(StaticFiles):
    """The built PWA. App routes such as `/e/12?t=30` (deep links) get `index.html`, so a
    cold start on a link works; names with an extension, and unknown `/api/…` routes, stay
    real 404s."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or "." in Path(path).name or Path(path).parts[:1] == ("api",):
                raise
            response = await super().get_response("index.html", scope)
        if not path.startswith("assets/"):
            # The shell must revalidate so a new build reaches the phone. Vite's assets/ have
            # content hashes in their names, so they may be cached as they are.
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    config: Config | None = None,
    services: Callable[[Config], Services] = build_services,
) -> FastAPI:
    config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        svc = services(config)
        app.state.services = svc
        await svc.start()
        try:
            yield
        finally:
            await svc.stop()

    app = FastAPI(title="Hearsay", version=__version__, lifespan=lifespan)
    app.include_router(router)
    register_error_handlers(app)
    if config.web_dist_dir.is_dir():
        app.mount("/", AppFiles(directory=config.web_dist_dir, html=True), name="web")
    return app
