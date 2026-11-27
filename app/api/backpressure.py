"""Splits large response bodies so the server's flow control reaches the handler.

uvicorn awaits its write drain *before* a write, so ``send`` with a whole multi-megabyte body
returns at once, the handler finishes and its limiter slots are released while the body still
sits in the transport buffer of a client that does not read. Sending the body in pieces of
``CHUNK_SIZE`` makes ``send`` block whenever the client stops reading, so the request (and the
concurrency slots held by ``RateLimitMiddleware`` further in) stays alive until the client has
taken the data or disconnects. The bytes on the wire, the headers and ``more_body`` of the
last piece are unchanged.

Pure ASGI, outermost middleware: it sees exactly what goes to the client (after gzip).
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["CHUNK_SIZE", "BackpressureMiddleware"]

# Pieces of at most this many bytes (the server pauses writing above its high-water mark).
CHUNK_SIZE = 64 * 1024


class BackpressureMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def split_send(message: Message) -> None:
            body = message.get("body", b"") if message["type"] == "http.response.body" else b""
            if len(body) <= CHUNK_SIZE:
                await send(message)
                return
            view = memoryview(body)  # slices share the memory: no copies of the body
            more_body = message.get("more_body", False)
            last = len(view)
            for start in range(0, last, CHUNK_SIZE):
                end = min(start + CHUNK_SIZE, last)
                piece: Message = {
                    **message,
                    "body": view[start:end],
                    "more_body": True if end < last else more_body,
                }
                await send(piece)

        await self.app(scope, receive, split_send)
