"""A concurrency slot is held until the response body is delivered (docs/API.md "Одновременные
запросы (этап 18B)" and "Одновременные запросы вне API": "Место занято, пока ответ не передан
клиенту целиком").

The bypass: a body sent as ONE `http.response.body` message lets the handler finish, and so frees
the slot, although the client has not read a byte (uvicorn waits for the transport to drain
*before* a write, not after). The fix splits big body messages into pieces of at most 64 KiB so
that flow control keeps the handler blocked.

`Wire` below is the server side of a connection with that uvicorn behaviour: a `send` of a body
message first waits until the transport is drained; once more than 64 KiB were written and the
client "does not read", the transport is paused and the next send blocks.

- 1: N unread big responses hold N slots, request N+1 is refused; a read frees exactly one
  (API v2, API v1, page).
- 2: the bytes, status and headers the client gets are the same as with the limiter off,
  for body sizes around 64 KiB, HEAD, 304, gzip and a streaming response.
- 3: no body message is larger than 64 KiB; the last one keeps `more_body` false.
- 4: the v1 response-size surcharge is unchanged.
- 5: cancelling in the middle of a split body releases the slots exactly once.
- A real uvicorn server with sockets that never read.
"""

import asyncio
import json
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse

from tests._apikeys import (
    BLOB_V1,
    IP_A,
    IP_B,
    SMALL_V1,
    V2_BODY_429,
    KeysApp,
    remaining,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

PIECE = 64 * 1024
MIB = 1024 * 1024
N = 2
MakeApp = Callable[..., Any]
MakeEnv = Callable[..., Awaitable[KeysApp]]

BIG_API = "/api/v2/__big"
BIG_PAGE = "/__page_big"
SMALL_PAGE = "/__page_small"
BIG_PATHS = [
    pytest.param(BIG_API, id="api-v2"),
    pytest.param(BLOB_V1, id="api-v1"),
    pytest.param(BIG_PAGE, id="page"),
]
SIZE_PATHS = [pytest.param(BIG_API, "api", id="api-v2"), pytest.param(BIG_PAGE, "page", id="page")]


def pattern(n: int) -> bytes:
    return (bytes(range(256)) * (n // 256 + 1))[:n]


def add_routes(app: FastAPI) -> None:
    """Routes that answer with one big `Response(bytes)` like the real handlers do."""

    def big(n: int = 0) -> Response:
        return Response(
            content=pattern(n),
            media_type="application/octet-stream",
            headers={"ETag": f'"big-{n}"', "Cache-Control": "public, max-age=60"},
        )

    def not_modified() -> Response:
        return Response(
            status_code=304, headers={"ETag": '"nm"', "Cache-Control": "public, max-age=60"}
        )

    def chunks() -> StreamingResponse:
        async def body() -> Any:
            for i in range(5):
                yield bytes([65 + i]) * 100_000

        return StreamingResponse(body(), media_type="application/octet-stream")

    def small() -> Response:
        return Response(content=b"ok", media_type="text/plain")

    before = len(app.router.routes)
    for prefix in ("/api/v2/__", "/__page_"):
        app.add_api_route(f"{prefix}big", big, methods=["GET", "HEAD"], include_in_schema=False)
        app.add_api_route(
            f"{prefix}nm", not_modified, methods=["GET", "HEAD"], include_in_schema=False
        )
        app.add_api_route(
            f"{prefix}chunks", chunks, methods=["GET", "HEAD"], include_in_schema=False
        )
    app.add_api_route(SMALL_PAGE, small, methods=["GET", "HEAD"], include_in_schema=False)
    added = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[0:0] = added


@pytest.fixture
async def make_env(make_keys_app: MakeApp) -> MakeEnv:
    async def make(**extra: Any) -> KeysApp:
        values: dict[str, Any] = {
            "rate_limit_concurrent": N,
            "rate_limit_page_concurrent": N,
            "rate_limit_per_minute": 1_000_000,
            "rate_limit_per_hour": 1_000_000,
            "rate_limit_v1_bytes_per_request": 10**9,
            "rate_limit_v1_raw_bytes_per_request": 10**9,
            **extra,
        }
        env: KeysApp = await make_keys_app(**values)
        add_routes(env.app)
        return env

    return make


# --- the server side of a connection ---


class Wire:
    """`send` of a connection whose client reads (`reading`) or does not read."""

    def __init__(self, *, reading: bool = True) -> None:
        self.reading = reading
        self.messages: list[dict[str, Any]] = []
        self.drained = asyncio.Event()
        self.drained.set()
        self.blocked = asyncio.Event()
        self.buffered = 0

    async def send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            if not self.drained.is_set():
                self.blocked.set()
                await self.drained.wait()
            self.buffered += len(message.get("body", b""))
            if not self.reading and self.buffered > PIECE:
                self.drained.clear()
        self.messages.append(message)

    def read_all(self) -> None:
        self.reading = True
        self.buffered = 0
        self.drained.set()

    @property
    def status(self) -> int:
        return int(self.messages[0]["status"])

    @property
    def headers(self) -> dict[str, str]:
        return {k.decode().lower(): v.decode() for k, v in self.messages[0]["headers"]}

    @property
    def pieces(self) -> list[dict[str, Any]]:
        return [m for m in self.messages if m["type"] == "http.response.body"]

    @property
    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.pieces)


def call(
    env: KeysApp,
    path: str,
    wire: Wire,
    *,
    ip: str = IP_A,
    method: str = "GET",
    query: bytes = b"",
    accept: str = "identity",
) -> "asyncio.Task[None]":
    """Run one request through the whole application with `wire` as the connection."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": [(b"host", b"test"), (b"accept-encoding", accept.encode())],
        "client": (ip, 40000),
        "server": ("test", 80),
    }
    state = {"sent": False}
    never = asyncio.Event()

    async def receive() -> dict[str, Any]:
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await never.wait()
        return {"type": "http.disconnect"}

    return asyncio.ensure_future(env.app(scope, receive, wire.send))


async def held(env: KeysApp, count: int, path: str, ip: str = IP_A) -> list[tuple[Wire, Any]]:
    """`count` requests whose big response the client does not read; wait until all block."""
    query = f"n={MIB}".encode()
    pairs = [(Wire(reading=False), None) for _ in range(count)]
    out = []
    for wire, _ in pairs:
        task = call(env, path, wire, ip=ip, query=query)
        out.append((wire, task))
    for wire, task in out:
        waiter = asyncio.ensure_future(wire.blocked.wait())
        await asyncio.wait({waiter, task}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
        assert wire.blocked.is_set(), (
            "the request finished although the client read nothing: its slot was freed early "
            f"(status {wire.status if wire.messages else '?'})"
        )
    return out


async def refused(env: KeysApp, path: str, ip: str = IP_A) -> Wire:
    wire = Wire()
    await call(env, path, wire, ip=ip, query=f"n={MIB}".encode())
    return wire


def assert_429(wire: Wire, *, api: bool) -> None:
    assert wire.status == 429, wire.status
    assert json.loads(wire.body) == V2_BODY_429 or json.loads(wire.body) == {
        "errors": "Rate limit exceeded"
    }
    assert wire.headers["retry-after"] == "1"
    assert wire.headers["cache-control"] == "no-store"
    assert ("x-ratelimit-limit" in wire.headers) is api


async def finish(pairs: list[tuple[Wire, Any]]) -> None:
    for wire, _ in pairs:
        wire.read_all()
    await asyncio.gather(*(task for _, task in pairs), return_exceptions=True)


# --- 1: unread big responses hold their slots ---


@pytest.mark.parametrize("path", BIG_PATHS)
async def test_unread_big_responses_hold_n_slots_and_the_next_request_is_refused(
    make_env: MakeEnv, path: str
) -> None:
    env = await make_env()
    pairs = await held(env, N, path)

    extra = await refused(env, path)
    other_address = await refused(env, path, IP_B)  # another client is not affected
    await finish(pairs)

    assert_429(extra, api=path != BIG_PAGE)
    assert other_address.status == 200
    assert extra.messages  # a real answer, not an exception


@pytest.mark.parametrize("path", BIG_PATHS)
async def test_one_read_response_frees_exactly_one_slot(make_env: MakeEnv, path: str) -> None:
    env = await make_env()
    pairs = await held(env, N, path)
    first_wire, first_task = pairs[0]
    first_wire.read_all()
    await first_task

    admitted = await held(env, 1, path)  # exactly one place is free again
    full = await refused(env, path)
    await finish(pairs[1:] + admitted)

    assert_429(full, api=path != BIG_PAGE)


@pytest.mark.parametrize("path", BIG_PATHS)
async def test_all_slots_are_free_after_every_response_is_read(
    make_env: MakeEnv, path: str
) -> None:
    env = await make_env()
    await finish(await held(env, N, path))

    again = await held(env, N, path)
    full = await refused(env, path)
    await finish(again)

    assert_429(full, api=path != BIG_PAGE)


async def test_page_and_api_counters_stay_separate_for_unread_big_responses(
    make_env: MakeEnv,
) -> None:
    env = await make_env()
    pages = await held(env, N, BIG_PAGE)

    api = await refused(env, BIG_API)
    page_full = await refused(env, BIG_PAGE)
    await finish(pages)

    assert api.status == 200
    assert_429(page_full, api=False)


# --- 2 and 3: what the client gets does not change ---

SIZES = [1000, PIECE - 1, PIECE, PIECE + 1, 3 * MIB]
COMPARED = ("content-length", "etag", "cache-control", "content-encoding", "content-type", "vary")


async def fetch(
    env: KeysApp, url: str, *, method: str = "GET", accept: str = "identity"
) -> tuple[int, dict[str, str | None], bytes]:
    response = await env.client(IP_A, {"Accept-Encoding": accept}).request(method, url)
    return response.status_code, {k: response.headers.get(k) for k in COMPARED}, response.content


@pytest.mark.parametrize(("path", "kind"), SIZE_PATHS)
async def test_client_gets_the_same_response_as_without_the_limiter(
    make_env: MakeEnv, path: str, kind: str
) -> None:
    on = await make_env()
    off = await make_env(rate_limit_enabled=False)
    prefix = path[: -len("big")]
    cases: list[tuple[str, str, str, bytes]] = []
    for size in SIZES:
        for accept in ("identity", "gzip"):
            cases.append((f"{path}?n={size}", "GET", accept, pattern(size)))
        cases.append((f"{path}?n={size}", "HEAD", "identity", b""))
    cases.append((f"{prefix}nm", "GET", "identity", b""))
    cases.append(
        (
            f"{prefix}chunks",
            "GET",
            "identity",
            b"".join(bytes([65 + i]) * 100_000 for i in range(5)),
        )
    )
    cases.append(
        (f"{prefix}chunks", "GET", "gzip", b"".join(bytes([65 + i]) * 100_000 for i in range(5)))
    )

    for url, method, accept, expected in cases:
        got = await fetch(on, url, method=method, accept=accept)
        before = await fetch(off, url, method=method, accept=accept)
        label = f"{method} {url} {accept}"
        assert got == before, label
        assert got[2] == expected, label
        if url.endswith("nm"):
            assert got[0] == 304, label
        else:
            assert got[0] == 200, label


@pytest.mark.parametrize(("path", "kind"), SIZE_PATHS)
async def test_body_messages_are_at_most_64_kib_and_the_last_one_ends_the_body(
    make_env: MakeEnv, path: str, kind: str
) -> None:
    env = await make_env()
    prefix = path[: -len("big")]
    for size in SIZES:
        wire = Wire()
        await call(env, path, wire, query=f"n={size}".encode())

        assert wire.status == 200, size
        assert all(len(m.get("body", b"")) <= PIECE for m in wire.pieces), size
        assert wire.pieces[-1].get("more_body", False) is False, size
        assert all(m.get("more_body", False) for m in wire.pieces[:-1]), size
        assert wire.body == pattern(size), size
        assert wire.headers["content-length"] == str(size), size
        if size >= 2 * PIECE:
            assert len(wire.pieces) >= size // PIECE, size  # really split

    head = Wire()
    await call(env, path, head, method="HEAD", query=f"n={3 * MIB}".encode())
    if kind == "api":  # the caching layer drops the HEAD body; on pages uvicorn does on the wire
        assert head.body == b""
    assert all(len(m.get("body", b"")) <= PIECE for m in head.pieces)
    assert head.pieces[-1].get("more_body", False) is False
    assert head.headers["content-length"] == str(3 * MIB)

    stream = Wire()
    await call(env, f"{prefix}chunks", stream)
    assert stream.body == b"".join(bytes([65 + i]) * 100_000 for i in range(5))
    assert all(len(m.get("body", b"")) <= PIECE for m in stream.pieces)
    assert stream.pieces[-1].get("more_body", False) is False


# --- 4: the v1 surcharge is unchanged ---


@pytest.mark.parametrize(
    ("n", "cost"),
    [(300_000, 2), (3 * MIB, 12)],
    ids=["two-wire-units", "twelve-wire-units"],
)
@pytest.mark.parametrize("via", ["httpx", "asgi-wire"])
async def test_v1_big_response_is_charged_as_before(
    make_env: MakeEnv, n: int, cost: int, via: str
) -> None:
    """Cost = the larger of ceil(wire bytes / 256 KiB) and ceil(raw bytes / 1 MiB), >= 1."""
    env = await make_env(
        rate_limit_per_minute=100,
        rate_limit_per_hour=1000,
        rate_limit_v1_bytes_per_request=262_144,
        rate_limit_v1_raw_bytes_per_request=MIB,
    )
    if via == "httpx":
        first = await env.client(IP_A, {"Accept-Encoding": "identity"}).get(
            BLOB_V1, params={"n": n}
        )
        assert first.status_code == 200
    else:
        wire = Wire()
        await call(env, BLOB_V1, wire, query=f"n={n}".encode())
        assert wire.status == 200

    follow_up = await env.client(IP_A).get(SMALL_V1)

    assert remaining(follow_up) == 100 - cost - 1


# --- 5: cancelling in the middle of a split body ---


@pytest.mark.parametrize("path", BIG_PATHS)
async def test_cancelled_requests_release_their_slots_exactly_once(
    make_env: MakeEnv, path: str
) -> None:
    env = await make_env()
    pairs = await held(env, N, path)
    for _, task in pairs:
        task.cancel()
    await asyncio.gather(*(task for _, task in pairs), return_exceptions=True)

    again = await held(env, N, path)  # all N places are free: nothing leaked
    full = await refused(env, path)  # not more than N: nothing released twice
    await finish(again)

    assert_429(full, api=path != BIG_PAGE)


# --- a real server and sockets that never read ---


async def raw_get(loop: asyncio.AbstractEventLoop, port: int, path: str) -> tuple[int, str, bytes]:
    """One `Connection: close` request over a raw socket (httpx is blocked by the respx guard)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await asyncio.wait_for(loop.sock_connect(sock, ("127.0.0.1", port)), timeout=5)
        await loop.sock_sendall(
            sock, f"GET {path} HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n".encode()
        )
        data = b""
        while True:
            chunk = await asyncio.wait_for(loop.sock_recv(sock, 65536), timeout=5)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    head, _, body = data.partition(b"\r\n\r\n")
    text = head.decode("latin-1")
    return int(text.split(" ", 2)[1]), text + "\r\n", body


@pytest.fixture
def restore_uvicorn_loggers():
    """uvicorn.Config mutates the global uvicorn loggers even with log_config=None."""
    names = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")
    saved = {}
    for name in names:
        lg = logging.getLogger(name)
        saved[name] = (lg.level, list(lg.handlers), lg.propagate, lg.disabled)
    yield
    for name, (level, handlers, propagate, disabled) in saved.items():
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.handlers[:] = handlers
        lg.propagate = propagate
        lg.disabled = disabled


async def test_real_server_unread_big_responses_hold_their_slots(
    make_env: MakeEnv, restore_uvicorn_loggers: None
) -> None:
    """Raw sockets send requests and read nothing; once N responses are stuck in the server, a
    fresh request from the same address is refused. Polls (bounded) instead of sleeping blind."""
    import uvicorn

    env = await make_env(rate_limit_page_concurrent=N)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
    except OSError as error:  # pragma: no cover - environment without local sockets
        listener.close()
        pytest.skip(f"cannot bind a local port: {error}")
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        # log_config=None keeps uvicorn from reconfiguring global logging
        # (dictConfig would leak into later tests in the same process).
        uvicorn.Config(
            env.app,
            lifespan="off",
            log_config=None,
            log_level="warning",
            access_log=False,
        )
    )
    serve = asyncio.ensure_future(server.serve(sockets=[listener]))
    loop = asyncio.get_running_loop()
    clients: list[socket.socket] = []
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started, "the test server did not start"
        request = (
            f"GET {BIG_PAGE}?n={16 * MIB} HTTP/1.1\r\nHost: t\r\nConnection: close\r\n"
            "Accept-Encoding: identity\r\n\r\n"
        ).encode()
        for _ in range(N):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            sock.setblocking(False)
            await loop.sock_connect(sock, ("127.0.0.1", port))
            await loop.sock_sendall(sock, request)
            clients.append(sock)  # and never read

        status = 0
        head = ""
        body = b""
        for _ in range(160):  # up to ~8 s for the N responses to get stuck
            status, head, body = await raw_get(loop, port, SMALL_PAGE)
            if status == 429:
                break
            await asyncio.sleep(0.05)

        assert status == 429, "the slots of unread big responses were freed"
        assert json.loads(body) == V2_BODY_429
        lowered = head.lower()
        assert "\r\nretry-after: 1\r\n" in lowered
        assert "\r\ncache-control: no-store\r\n" in lowered
        assert "x-ratelimit" not in lowered
    finally:
        for sock in clients:
            sock.close()
        server.should_exit = True
        try:
            await asyncio.wait_for(serve, timeout=10)
        finally:
            listener.close()
