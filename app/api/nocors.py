"""No CORS on the key endpoints (docs/API.md, "Ключи API (этап 20B)").

The CORS middleware answers every request that carries an ``Origin`` with
``Access-Control-Allow-Origin``. For the endpoints that mail links, issue keys and delete data
that would let any site call them from a visitor's browser, so this middleware, placed outside
it, removes every ``Access-Control-*`` header from their responses (the web interface calls
them from its own origin).
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PREFIXES: tuple[str, ...] = ("/api/v2/keys", "/api/v2/account")


def _applies(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in PREFIXES)


class NoCorsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _applies(scope["path"]):
            await self.app(scope, receive, send)
            return

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name in [n for n in headers if n.lower().startswith("access-control-")]:
                    del headers[name]
            await send(message)

        await self.app(scope, receive, wrapped)
