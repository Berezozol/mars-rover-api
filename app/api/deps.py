"""Shared FastAPI dependencies of the routers."""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.api.apikeys import rate_identity, site_key
from app.core.overload import HeavyQueryGate


def get_heavy_query_gate(request: Request) -> HeavyQueryGate:
    """The server-wide gate of heavy searches (``app.state.heavy_query_gate``)."""
    return cast(HeavyQueryGate, request.app.state.heavy_query_gate)


def get_client_key(request: Request) -> str:
    """The client key of the heavy-search gate: the account of a valid API key, else the
    client site (IPv6 reduced to its /48, like the other per-IP abuse counters)."""
    identity = rate_identity(request.scope)
    if identity.kind == "account":
        return identity.key
    return site_key(request.scope)


ClientKey = Annotated[str, Depends(get_client_key)]
