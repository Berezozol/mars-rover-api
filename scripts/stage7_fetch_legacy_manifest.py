"""Stage 7: fetch the archived old-API manifest of a rover from the Wayback Machine.

Owner-approved, small: 1 availability request + 1 snapshot request (redirects counted).
Saves raw JSON to data/legacy/<rover>-manifest.json and prints a compact summary.
"""

import json
import sys
import time
from pathlib import Path

import httpx

UA = "mars-rover-api-dev"
OLD = "https://api.nasa.gov/mars-photos/api/v1/manifests/{rover}?api_key=DEMO_KEY"
OUT = Path("data/legacy")


def main(rover: str) -> int:
    requests = 0
    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=False) as client:
        resp = client.get(
            "https://archive.org/wayback/available", params={"url": OLD.format(rover=rover)}
        )
        requests += 1
        snap = resp.json().get("archived_snapshots", {}).get("closest")
        print(f"availability: HTTP {resp.status_code}, closest={snap and snap.get('timestamp')}")
        if not snap or not snap.get("available"):
            print(f"requests used: {requests}")
            return 1
        url = f"https://web.archive.org/web/{snap['timestamp']}id_/{OLD.format(rover=rover)}"
        for _ in range(3):
            time.sleep(1.1)
            resp = client.get(url)
            requests += 1
            if resp.is_redirect:
                url = str(resp.next_request.url) if resp.next_request else resp.headers["location"]
                print(f"redirect -> {url}")
                continue
            break
    print(
        f"snapshot: HTTP {resp.status_code}, {len(resp.content)} bytes; requests used: {requests}"
    )
    data = json.loads(resp.content)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{rover}-manifest.json"
    path.write_bytes(resp.content)
    m = data["photo_manifest"]
    photos = m["photos"]
    print(
        f"saved {path}: max_sol={m.get('max_sol')} max_date={m.get('max_date')} "
        f"total_photos={m.get('total_photos')} sols={len(photos)} "
        f"sum={sum(p['total_photos'] for p in photos)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
