"""Smoke test of a deployed Mars Rover API, run from any machine with network access.

Usage:
    uv run python deploy/smoke.py https://mars-api.example.ts.net

Needs only httpx and the standard library. Sends 16 requests (12 of them count against the
per-IP rate limit of 60/min), never follows redirects. With --with-mer-image it also
fetches a full-size Opportunity image (a PDS download and conversion, one more request).
Exit code 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

V1_PHOTOS = "/mars-photos/api/v1/rovers/curiosity/photos?sol=1000&camera=fhaz&api_key=DEMO_KEY"
V1_ROVERS = "/api/v1/rovers"
V1_MANIFEST = "/api/v1/manifests/perseverance"
V1_UNKNOWN_ROVER = "/api/v1/rovers/notarover/photos?sol=1000"
V1_TRAILING_SLASH = "/api/v1/rovers/curiosity/"
V2_ROVERS = "/api/v2/rovers"
V2_PHOTOS = "/api/v2/rovers/curiosity/photos?per_page=1"
V2_MER_PHOTOS = "/api/v2/rovers/opportunity/photos?sol=1&per_page=1"
EXPECTED_ROVERS = {"curiosity", "opportunity", "perseverance", "spirit"}
# v2 also lists the Ingenuity helicopter (stage 12); v1 keeps the original four.
EXPECTED_V2_ROVERS = EXPECTED_ROVERS | {"ingenuity"}


class CheckFailed(Exception):
    """A check found the deployment misbehaving."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise CheckFailed(f"body is not JSON: {response.text[:200]!r}") from exc


def _status(response: httpx.Response, *expected: int) -> None:
    if response.status_code in expected:
        return
    hint = ""
    if response.status_code == 429:
        hint = f" (rate limited, Retry-After: {response.headers.get('retry-after')})"
    raise CheckFailed(
        f"HTTP {response.status_code}, expected {' or '.join(map(str, expected))}{hint}; "
        f"body: {response.text[:200]!r}"
    )


class Smoke:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.https = urlsplit(self.base_url).scheme == "https"
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "mars-rover-api-smoke/1.0"},
        )
        self.requests = 0
        self.failures = 0
        self.photo_id: int | None = None
        self.mer_photo_id: int | None = None

    def get(self, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
        self.requests += 1
        return self.client.get(path, headers=headers)

    def run(self, name: str, check: Callable[[], str]) -> None:
        try:
            detail = check()
        except CheckFailed as exc:
            self.failures += 1
            print(f"FAIL  {name}: {exc}")
        except (httpx.HTTPError, KeyError, TypeError, AttributeError, ValueError) as exc:
            # Network errors and response shapes the checks did not expect.
            self.failures += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {name}" + (f": {detail}" if detail else ""))

    # --- checks ------------------------------------------------------------------------------

    def health(self) -> str:
        r = self.get("/health")
        _status(r, 200)
        body = _json(r)
        _expect(body == {"status": "ok", "db": "ok"}, f"unexpected body {body}")
        return ""

    def home_page(self) -> str:
        r = self.get("/")
        _status(r, 200)
        ctype = r.headers.get("content-type", "")
        _expect(ctype.startswith("text/html"), f"content-type {ctype!r}, expected text/html")
        _expect('id="app"' in r.text, 'no id="app": image built without the frontend dist?')
        match = re.search(r'src="(/assets/[^"]+\.js)"', r.text)
        _expect(match is not None, "no /assets/*.js script in index.html")
        asset = self.get(match.group(1))
        _status(asset, 200)
        cache = asset.headers.get("cache-control", "")
        _expect("immutable" in cache, f"{match.group(1)}: Cache-Control {cache!r}, no immutable")
        return f"{len(r.content)} bytes, asset {match.group(1)}"

    def developers_page(self) -> str:
        r = self.get("/developers")
        _status(r, 200)
        ctype = r.headers.get("content-type", "")
        _expect(ctype.startswith("text/html"), f"content-type {ctype!r}, expected text/html")
        _expect('id="app"' not in r.text, "/developers serves the interface, not the dev page")
        return f"{len(r.content)} bytes"

    def llms_txt(self) -> str:
        r = self.get("/llms.txt")
        _status(r, 200)
        _expect(
            self.base_url in r.text,
            f"/llms.txt does not contain {self.base_url}: PUBLIC_BASE_URL unset or wrong "
            "(when smoke-testing a local address against a production .env this is expected)",
        )
        return ""

    def health_sync(self) -> str:
        r = self.get("/health/sync")
        # 503 means stale data: worth a look, but the deployment itself answers correctly.
        _status(r, 200, 503)
        body = _json(r)
        print(f"      /health/sync -> {r.status_code} {json.dumps(body)}")
        return "data is fresh" if r.status_code == 200 else "WARNING: data is stale (503)"

    def v1_photos(self) -> str:
        r = self.get(V1_PHOTOS)
        _status(r, 200)
        photos = _json(r).get("photos")
        _expect(isinstance(photos, list), "no 'photos' list")
        _expect(len(photos) == 2, f"{len(photos)} photos, expected 2")
        dates = {p.get("earth_date") for p in photos}
        _expect(dates == {"2015-05-30"}, f"earth_date {sorted(map(str, dates))}, want 2015-05-30")
        return "2 photos, earth_date 2015-05-30"

    def v1_rovers(self) -> str:
        r = self.get(V1_ROVERS)
        _status(r, 200)
        rovers = _json(r).get("rovers")
        _expect(isinstance(rovers, list), "no 'rovers' list")
        names = {str(x.get("name", "")).lower() for x in rovers}
        _expect(names == EXPECTED_ROVERS, f"rovers {sorted(names)}")
        return "4 rovers"

    def v1_manifest(self) -> str:
        r = self.get(V1_MANIFEST)
        _status(r, 200)
        manifest = _json(r).get("photo_manifest")
        _expect(isinstance(manifest, dict), "no 'photo_manifest' object")
        _expect(manifest.get("name") == "Perseverance", f"name {manifest.get('name')!r}")
        sols = manifest.get("photos")
        _expect(isinstance(sols, list) and sols, "empty 'photos' in manifest")
        return f"max_sol {manifest.get('max_sol')}, total_photos {manifest.get('total_photos')}"

    def v2_rovers(self) -> str:
        r = self.get(V2_ROVERS)
        _status(r, 200)
        items = _json(r).get("items")
        _expect(isinstance(items, list), "no 'items' list")
        ids = {x.get("id") for x in items}
        _expect(ids == EXPECTED_V2_ROVERS, f"rovers {sorted(map(str, ids))}")
        return f"{len(EXPECTED_V2_ROVERS)} rovers"

    def v2_photos(self) -> str:
        r = self.get(V2_PHOTOS)
        _status(r, 200)
        body = _json(r)
        items = body.get("items")
        _expect(isinstance(items, list) and len(items) == 1, f"items {items!r}, expected 1")
        self.photo_id = items[0]["id"]
        return f"photo id {self.photo_id}, total {body.get('total')}"

    def v2_image_redirect(self) -> str:
        _expect(self.photo_id is not None, "skipped: no photo id from the previous check")
        r = self.get(f"/api/v2/photos/{self.photo_id}/image")
        _status(r, 302)
        location = r.headers.get("location", "")
        _expect(location.startswith(("https://", "http://")), f"Location {location!r}")
        return location

    def v2_mer_full(self) -> str:
        r = self.get(V2_MER_PHOTOS)
        _status(r, 200)
        items = _json(r).get("items")
        _expect(isinstance(items, list) and len(items) == 1, f"items {items!r}, expected 1")
        self.mer_photo_id = items[0]["id"]
        full = items[0]["images"]["full"]
        path = f"/api/v2/photos/{self.mer_photo_id}/image"
        _expect(
            urlsplit(full).path == path and "size=full" in full,
            f"images.full {full!r}, expected our {path}?size=full",
        )
        return full

    def v2_mer_image(self) -> str:
        _expect(self.mer_photo_id is not None, "skipped: no MER photo id from the previous check")
        r = self.get(f"/api/v2/photos/{self.mer_photo_id}/image?size=full")
        _status(r, 200)
        ctype = r.headers.get("content-type", "")
        _expect(ctype.startswith("image/jpeg"), f"content-type {ctype!r}, expected image/jpeg")
        _expect(r.content[:2] == b"\xff\xd8", "body is not a JPEG")
        return f"{len(r.content)} bytes"

    def v1_trailing_slash(self) -> str:
        r = self.get(V1_TRAILING_SLASH)
        _status(r, 307)
        location = r.headers.get("location", "")
        _expect(bool(location), "no Location header")
        if self.https:
            # http here means the proxy's X-Forwarded-Proto is not trusted (FORWARDED_ALLOW_IPS).
            _expect(location.startswith("https://"), f"Location {location!r} is not https")
        return location

    def etag_not_modified(self) -> str:
        first = self.get(V2_ROVERS)
        _status(first, 200)
        etag = first.headers.get("etag")
        _expect(bool(etag), "no ETag header")
        second = self.get(V2_ROVERS, headers={"If-None-Match": etag})
        _status(second, 304)
        return f"ETag {etag}"

    def v1_unknown_rover(self) -> str:
        r = self.get(V1_UNKNOWN_ROVER)
        _status(r, 400)
        body = _json(r)
        _expect(body == {"errors": "Invalid Rover Name"}, f"body {body}")
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test of a deployed Mars Rover API.")
    parser.add_argument("base_url", help="for example https://mars-api.example.ts.net")
    parser.add_argument(
        "--with-mer-image",
        action="store_true",
        help="also GET the full-size Opportunity image (PDS download and conversion, slow)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per request")
    args = parser.parse_args(argv)

    if urlsplit(args.base_url).scheme not in ("http", "https"):
        parser.error("base_url must start with http:// or https://")

    smoke = Smoke(args.base_url, args.timeout)
    print(f"Smoke test of {smoke.base_url}")
    redirect_name = "v1 trailing slash -> 307" + (" to https" if smoke.https else "")
    checks: list[tuple[str, Callable[[], str]]] = [
        ("GET /health -> 200", smoke.health),
        ("GET / -> 200 text/html", smoke.home_page),
        ("GET /developers -> 200 text/html", smoke.developers_page),
        ("GET /llms.txt contains the base URL", smoke.llms_txt),
        ("GET /health/sync -> 200 or 503", smoke.health_sync),
        ("v1 photos curiosity sol=1000 camera=fhaz", smoke.v1_photos),
        ("v1 rovers", smoke.v1_rovers),
        ("v1 manifest perseverance", smoke.v1_manifest),
        ("v2 rovers", smoke.v2_rovers),
        ("v2 photos per_page=1", smoke.v2_photos),
        ("v2 image redirect -> 302", smoke.v2_image_redirect),
        ("v2 opportunity images.full is ours", smoke.v2_mer_full),
        (redirect_name, smoke.v1_trailing_slash),
        ("If-None-Match -> 304", smoke.etag_not_modified),
        ("v1 unknown rover -> 400", smoke.v1_unknown_rover),
    ]
    if args.with_mer_image:
        checks.append(("v2 opportunity full image -> 200 jpeg", smoke.v2_mer_image))
    with smoke.client:
        for name, check in checks:
            smoke.run(name, check)

    total = len(checks)
    print(f"\n{total - smoke.failures}/{total} checks passed, {smoke.requests} requests sent.")
    return 1 if smoke.failures else 0


if __name__ == "__main__":
    sys.exit(main())
