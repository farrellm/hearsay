"""How Hearsay's errors answer a request: each kind has one HTTP status, and its message is the
response's `detail`."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from hearsay.errors import HearsayError
from hearsay.feeds.parser import FeedError
from hearsay.feeds.service import AlreadySubscribedError

# Errors not listed here are bugs or outages a request can't fix, and stay a 500.
STATUS: dict[type[HearsayError], int] = {
    FeedError: 422,
    AlreadySubscribedError: 409,
}


async def _answer(_request: Request, exc: Exception) -> JSONResponse:
    status = next(STATUS[kind] for kind in type(exc).__mro__ if kind in STATUS)
    return JSONResponse({"detail": str(exc)}, status_code=status)


def register_error_handlers(app: FastAPI) -> None:
    for kind in STATUS:
        app.add_exception_handler(kind, _answer)
