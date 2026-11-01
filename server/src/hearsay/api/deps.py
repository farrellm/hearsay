"""What routes share: the services, and lookups that answer 404 for what isn't there."""

from typing import Annotated

from fastapi import Depends, Request

from hearsay.services import Services


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


Svc = Annotated[Services, Depends(services)]
