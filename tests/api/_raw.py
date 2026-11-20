"""Raw ASGI call helper: httpx would refuse or normalize a hostile Host header."""

from dataclasses import dataclass

from fastapi import FastAPI


@dataclass
class RawResponse:
    status: int
    headers: dict[str, str]
    body: str


async def raw_get(
    app: FastAPI, path: str, headers: list[tuple[bytes, bytes]], method: str = "GET"
) -> RawResponse:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("203.0.113.5", 50000),
        "server": ("testserver", 80),
    }
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    response_headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return RawResponse(int(start["status"]), response_headers, body.decode("utf-8"))
