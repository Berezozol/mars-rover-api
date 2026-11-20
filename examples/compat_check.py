#!/usr/bin/env python3
"""Check that typical requests of the archived Mars Rover Photos API work.

Standard library only. Sends one request at a time to the given base URL,
under both the /api/v1 and the /mars-photos/api/v1 prefixes, always with
?api_key=DEMO_KEY (the key is accepted and ignored). For each request it
prints OK or FAIL with the HTTP status, the response time and, on failure,
a short reason. Exit code 0 if every check passes, 1 otherwise.

Usage:
    python compat_check.py
    python compat_check.py https://your-host.example
    python compat_check.py --base-url http://127.0.0.1:8000
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "compat-check/1.0"
PREFIXES = ("/api/v1", "/mars-photos/api/v1")
API_KEY = "DEMO_KEY"
MAX_BODY = 10_000_000  # bytes; larger responses are treated as a failure

ROVER_KEYS = {
    "id": int,
    "name": str,
    "landing_date": str,
    "launch_date": str,
    "status": str,
    "max_sol": (int, type(None)),
    "max_date": (str, type(None)),
    "total_photos": int,
    "cameras": list,
}
CAMERA_KEYS = {"id": int, "name": str, "rover_id": int, "full_name": str}
PHOTO_KEYS = {
    "id": int,
    "sol": int,
    "camera": dict,
    "img_src": str,
    "earth_date": str,
    "rover": dict,
}
MANIFEST_KEYS = {k: v for k, v in ROVER_KEYS.items() if k not in ("id", "cameras")}
MANIFEST_KEYS["photos"] = list
MANIFEST_ENTRY_KEYS = {"sol": int, "earth_date": str, "total_photos": int, "cameras": list}


class Failure(Exception):
    """Raised by a validator with a short reason."""


def check_fields(obj, spec, where):
    if not isinstance(obj, dict):
        raise Failure(f"{where} is not an object")
    for key, types in spec.items():
        if key not in obj:
            raise Failure(f"{where} has no '{key}'")
        if not isinstance(obj[key], types) or isinstance(obj[key], bool):
            raise Failure(f"{where}.{key} has unexpected type {type(obj[key]).__name__}")


def check_rover(obj, where="rover"):
    check_fields(obj, ROVER_KEYS, where)
    for cam in obj["cameras"]:
        check_fields(cam, {"name": str, "full_name": str}, f"{where}.cameras[]")


def check_photo(obj, where="photo"):
    check_fields(obj, PHOTO_KEYS, where)
    check_fields(obj["camera"], CAMERA_KEYS, f"{where}.camera")
    check_rover(obj["rover"], f"{where}.rover")


def top_key(body, key):
    if not isinstance(body, dict) or key not in body:
        raise Failure(f"top-level key '{key}' is missing")
    return body[key]


def photo_list(body, key, expect_nonempty=True):
    photos = top_key(body, key)
    if not isinstance(photos, list):
        raise Failure(f"'{key}' is not a list")
    if expect_nonempty and not photos:
        raise Failure(f"'{key}' is empty (is data loaded for this rover/sol?)")
    for photo in photos[:5]:
        check_photo(photo, f"{key}[]")
    return photos


def v_rovers(body, state):
    rovers = top_key(body, "rovers")
    if not isinstance(rovers, list) or not rovers:
        raise Failure("'rovers' is not a non-empty list")
    for rover in rovers:
        check_rover(rover, "rovers[]")
    names = {r["name"] for r in rovers}
    missing = {"Curiosity", "Perseverance", "Opportunity", "Spirit"} - names
    if missing:
        raise Failure("missing rovers: " + ", ".join(sorted(missing)))


def v_rover(body, state):
    rover = top_key(body, "rover")
    check_rover(rover)
    if rover["name"] != "Curiosity":
        raise Failure(f"rover.name is {rover['name']!r}, expected 'Curiosity'")


def v_photos_sol(body, state):
    photos = photo_list(body, "photos")
    for p in photos:
        if p["sol"] != 1000 or not p["camera"]["name"].startswith("FHAZ"):
            raise Failure("photo does not match sol=1000 / camera=fhaz")
    state["photo_id"] = photos[0]["id"]
    state["earth_date"] = photos[0]["earth_date"]


def v_photos_date(body, state):
    photos = photo_list(body, "photos")
    wanted = state.get("earth_date")
    for p in photos[:5]:
        if wanted and p["earth_date"] != wanted:
            raise Failure(f"earth_date {p['earth_date']} != requested {wanted}")


def v_latest(body, state):
    photo_list(body, "latest_photos")


def v_manifest(body, state):
    manifest = top_key(body, "photo_manifest")
    check_fields(manifest, MANIFEST_KEYS, "photo_manifest")
    if not manifest["photos"]:
        raise Failure("photo_manifest.photos is empty")
    for entry in manifest["photos"][:5]:
        check_fields(entry, MANIFEST_ENTRY_KEYS, "photo_manifest.photos[]")


def v_photo_id(body, state):
    check_photo(top_key(body, "photo"))
    if body["photo"]["id"] != state.get("photo_id"):
        raise Failure("returned photo id differs from the requested one")


def v_page1(body, state):
    photos = photo_list(body, "photos")
    if len(photos) > 25:
        raise Failure(f"page=1 returned {len(photos)} photos, expected at most 25")
    state["page1_ids"] = [p["id"] for p in photos]


def v_page2(body, state):
    page1 = state.get("page1_ids", [])
    photos = photo_list(body, "photos", expect_nonempty=False)
    if len(page1) < 25:
        # Page 1 is not full, so there is no page 2.
        if photos:
            raise Failure(f"page 1 had {len(page1)} photos but page=2 is not empty")
        return
    if not photos:
        raise Failure("page 1 had 25 photos but page=2 is empty")
    if len(photos) > 25:
        raise Failure(f"page=2 returned {len(photos)} photos, expected at most 25")
    if {p["id"] for p in photos} & set(page1):
        raise Failure("page=2 repeats photo ids from page=1")


def build_checks():
    """Return (label, path-builder, validator). Path builders run lazily so
    that the photo id and earth_date come from earlier responses."""
    q = f"api_key={API_KEY}"
    return [
        ("rovers list", lambda s: f"/rovers?{q}", v_rovers),
        ("rover", lambda s: f"/rovers/curiosity?{q}", v_rover),
        (
            "photos sol=1000 camera=fhaz",
            lambda s: f"/rovers/curiosity/photos?sol=1000&camera=fhaz&{q}",
            v_photos_sol,
        ),
        (
            "photos by earth_date",
            lambda s: (
                "/rovers/curiosity/photos?earth_date="
                + urllib.parse.quote(str(s.get("earth_date", "2015-5-30")), safe="")
                + f"&{q}"
            ),
            v_photos_date,
        ),
        ("latest_photos", lambda s: f"/rovers/curiosity/latest_photos?{q}", v_latest),
        ("manifest", lambda s: f"/manifests/curiosity?{q}", v_manifest),
        (
            "photo by id",
            lambda s: f"/photos/{s.get('photo_id', 0)}?{q}",
            v_photo_id,
        ),
        (
            "pagination page=1",
            lambda s: f"/rovers/curiosity/photos?sol=1000&page=1&{q}",
            v_page1,
        ),
        (
            "pagination page=2",
            lambda s: f"/rovers/curiosity/photos?sol=1000&page=2&{q}",
            v_page2,
        ),
        (
            "rover name in upper case",
            lambda s: f"/rovers/CURIOSITY?{q}",
            v_rover,
        ),
    ]


def read_limited(resp):
    data = resp.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise ValueError(f"response is larger than {MAX_BODY} bytes")
    return data


def fetch(url, timeout):
    """Return (status, body_bytes, seconds). Never raises on HTTP errors."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, data = resp.status, read_limited(resp)
    except urllib.error.HTTPError as err:
        status, data = err.code, read_limited(err)
    return status, data, time.monotonic() - start


def run_one(base, prefix, label, build, validate, state, timeout):
    path = prefix + build(state)
    full_label = f"{prefix}  {label}"
    try:
        status, data, secs = fetch(base + path, timeout)
    except Exception as exc:  # network error, timeout, bad URL
        print(f"FAIL  ---  {'':>7}  {full_label}: {type(exc).__name__}: {exc}")
        return False
    ms = f"{secs * 1000:.0f} ms"
    reason = None
    if status != 200:
        reason = f"expected status 200, got {status}"
        if status == 429:
            reason += " (rate limited, wait and retry)"
    else:
        try:
            body = json.loads(data)
            validate(body, state)
        except json.JSONDecodeError:
            reason = "body is not valid JSON"
        except Failure as exc:
            reason = str(exc)
    if reason:
        print(f"FAIL  {status}  {ms:>7}  {full_label}: {reason}")
        return False
    print(f"OK    {status}  {ms:>7}  {full_label}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base", nargs="?", help="base URL (default http://127.0.0.1:8000)")
    parser.add_argument("--base-url", dest="base_url", help="same as the positional argument")
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds per request")
    args = parser.parse_args()
    base = (args.base_url or args.base or "http://127.0.0.1:8000").rstrip("/")

    scheme = urllib.parse.urlparse(base).scheme
    if scheme not in ("http", "https"):
        parser.error(f"base URL must start with http:// or https:// (got {base!r})")

    print(f"Checking {base} ({USER_AGENT})")
    passed = failed = 0
    for prefix in PREFIXES:
        state = {}  # photo id and earth_date taken from earlier responses
        for label, build, validate in build_checks():
            if run_one(base, prefix, label, build, validate, state, args.timeout):
                passed += 1
            else:
                failed += 1
            time.sleep(0.2)  # one request at a time, with a short pause
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
