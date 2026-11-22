"""Shared FastAPI dependencies of the routers."""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.api.apikeys import rate_identity
from app.core.overload import HeavyQueryGate


def get_heavy_query_gate(request: Request) -> HeavyQueryGate:
    """The server-wide gate of heavy searches (``app.state.heavy_query_gate``)."""
    return cast(HeavyQueryGate, request.app.state.heavy_query_gate)


def get_client_key(request: Request) -> str:
    """The client key of the rate limiter: the account of a valid API key, else the client
    address (IPv6 reduced to its /64)."""
    return rate_identity(request.scope).key


ClientKey = Annotated[str, Depends(get_client_key)]
