"""The public origin (scheme + host) used in absolute links and in example URLs.

If ``PUBLIC_BASE_URL`` is set and valid it is ALWAYS the origin and the ``Host`` header is
ignored (no host-based cache poisoning). Without it (local development) the ``Host`` header is
used only when it is a plain hostname, IPv4 or bracketed IPv6 address with an optional port;
otherwise ``http://localhost`` is substituted, so a hostile value never reaches a page or a feed
and no internal address is revealed.
"""

import re
from urllib.parse import urlsplit

from fastapi import Request

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = rf"{_LABEL}(?:\.{_LABEL})*\.?"
_IPV6 = r"\[[0-9A-Fa-f:.]{2,45}\]"
_HOST_RE = re.compile(rf"(?:{_HOSTNAME}|{_IPV6})(?::[0-9]{{1,5}})?", re.ASCII)
_MAX_HOST_LENGTH = 260
_SCHEMES = ("http", "https")
_FALLBACK_ORIGIN = "http://localhost"


def is_valid_host(value: str) -> bool:
    """True for ``hostname|IPv4|[IPv6]`` with an optional port in 1..65535."""
    if not value or len(value) > _MAX_HOST_LENGTH or not _HOST_RE.fullmatch(value):
        return False
    if value.startswith("["):
        port = value.rpartition("]:")[2] if "]:" in value else ""
    else:
        port = value.partition(":")[2]
    return not port or 1 <= int(port) <= 65535


def valid_configured_origin(value: str | None) -> str | None:
    """A configured base URL reduced to ``scheme://host[:port]``, or None if unusable."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        netloc = parts.netloc
    except ValueError:
        return None
    if parts.scheme not in _SCHEMES or not is_valid_host(netloc):
        return None
    return f"{parts.scheme}://{netloc}"


def request_origin(request: Request) -> str:
    """The configured public origin, else the request's own when valid, else localhost.

    uvicorn applies trusted X-Forwarded-Proto/For to the scheme and client address.
    """
    settings = getattr(request.app.state, "settings", None)
    configured = valid_configured_origin(getattr(settings, "public_base_url", None))
    if configured:
        return configured
    host = request.headers.get("host", "")
    if request.url.scheme in _SCHEMES and is_valid_host(host):
        return f"{request.url.scheme}://{host}"
    return _FALLBACK_ORIGIN
