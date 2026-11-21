"""Mirror raw Mars weather files (MEDA, REMS) from the PDS Atmospheres node.

Usage:
    uv run python scripts/weather_download.py --mission all
    uv run python scripts/weather_download.py --mission meda --dry-run --only-range sol_1740_1859
    uv run python scripts/weather_download.py --mission rems --limit 3

Layout: data/weather_raw/<mission>/<path relative to the dataset root>.
Resumable (finished files are skipped; downloads go to .part, then renamed).
Polite: one sequential request at a time, at most one request per second.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

BASE = "https://atmos.nmsu.edu"
USER_AGENT = "weather-archive-mirror/1.0 (research mirror, polite crawler)"
MIN_INTERVAL = 1.0
MAX_ATTEMPTS = 6
OUT_ROOT = Path("data/weather_raw")


@dataclass(frozen=True)
class Source:
    mission: str
    root: str  # dataset root URL, ends with '/'
    subdir: str  # first-level directory to crawl, relative to the root
    file_re: re.Pattern[str]  # which files to keep (matched on the file name)
    depth: int  # directory levels between subdir and files (range, sol -> 2)


MEDA_ROOT = f"{BASE}/PDS/data/PDS4/Mars2020/mars2020_meda/"
REMS_ROOT = f"{BASE}/PDS/data/mslrem_1001/"
SOURCES = [
    # derived_env: all sensor CSV + xml labels and collection files.
    Source("meda", MEDA_ROOT, "data_derived_env/", re.compile(r"\.(csv|xml)$", re.I), 2),
    # ATS (air temperature) lives in data_calibrated_env (CAL_ATS); WS (wind) as CAL_WS.
    Source(
        "meda",
        MEDA_ROOT,
        "data_calibrated_env/",
        re.compile(r"(CAL_(ATS|WS)_.*\.(csv|xml)$|^collection_)", re.I),
        2,
    ),
    # REMS: only RMD (environmental record) products with labels, plus collection files.
    Source(
        "rems", REMS_ROOT, "DATA/", re.compile(r"(RMD.*\.(tab|lbl|xml)$|^collection_)", re.I), 2
    ),
]

ROW_RE = re.compile(
    r'<a href="([^"?][^"]*)">[^<]*</a></td><td[^>]*>[^<]*</td><td[^>]*>\s*([^<]*?)\s*</td>',
)


def parse_size(text: str) -> int | None:
    m = re.fullmatch(r"([\d.]+)([KMG]?)", text.strip())
    if not m:
        return None
    return int(float(m.group(1)) * {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}[m.group(2)])


class Client:
    def __init__(self) -> None:
        self.http = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=True,
        )
        self.last = 0.0
        self.requests = 0

    def _wait(self) -> None:
        delay = MIN_INTERVAL - (time.monotonic() - self.last)
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()
        self.requests += 1

    def _backoff(self, attempt: int, resp: httpx.Response | None) -> None:
        delay = min(2.0**attempt * 2, 120.0)
        if resp is not None and (ra := resp.headers.get("Retry-After", "")).isdigit():
            delay = max(delay, float(ra))
        print(f"  retry {attempt}/{MAX_ATTEMPTS} in {delay:.0f}s", flush=True)
        time.sleep(delay)

    def listing(self, url: str) -> list[tuple[str, int | None, bool]]:
        """Return (name, approx size, is_dir) for entries of a directory."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait()
            resp = None
            try:
                resp = self.http.get(url)
                if resp.status_code == 404:
                    return []
                if resp.status_code < 400:
                    break
            except httpx.TransportError as exc:
                print(f"  listing error {exc!r}", flush=True)
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"listing failed: {url}")
            self._backoff(attempt, resp)
        out = []
        for href, size in ROW_RE.findall(resp.text):
            href = html.unescape(href)
            if href.startswith("/") or href.startswith("http"):
                continue  # parent directory link
            out.append((unquote(href.rstrip("/")), parse_size(size), href.endswith("/")))
        return out

    def download(self, url: str, dest: Path) -> tuple[int, str]:
        """Download to .part then rename. Returns (bytes, status)."""
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        status = "error"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait()
            resp = None
            try:
                with self.http.stream("GET", url) as resp:
                    if resp.status_code == 404:
                        return 0, "404"
                    if resp.status_code < 400:
                        expected = int(resp.headers.get("Content-Length", -1))
                        got = 0
                        with part.open("wb") as fh:
                            for chunk in resp.iter_bytes(1 << 16):
                                fh.write(chunk)
                                got += len(chunk)
                        if expected >= 0 and got != expected:
                            status = "short"
                            part.unlink(missing_ok=True)
                        else:
                            part.replace(dest)
                            return got, "ok"
                    else:
                        status = str(resp.status_code)
            except httpx.TransportError as exc:
                status = type(exc).__name__
            if attempt == MAX_ATTEMPTS:
                break
            self._backoff(attempt, resp)
        part.unlink(missing_ok=True)
        return 0, status


def already_have(dest: Path, listed: int | None) -> bool:
    if not dest.is_file():
        return False
    if listed is None:
        return True
    # Listing sizes are rounded (K/M); allow ~10% + 1 KiB slack.
    return abs(dest.stat().st_size - listed) <= listed * 0.1 + 1024


def run(args: argparse.Namespace) -> int:
    client = Client()
    log_path = OUT_ROOT / "download-log.csv"
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    new_log = not log_path.exists()
    log_fh = log_path.open("a", newline="", encoding="utf-8")
    log = csv.writer(log_fh)
    if new_log:
        log.writerow(["url", "bytes", "status", "time"])

    missions = {"meda", "rems"} if args.mission == "all" else {args.mission}
    files_seen = files_done = files_skipped = files_failed = 0
    bytes_done = bytes_listed = 0
    started = time.monotonic()
    limit_hit = False

    def progress(frac: float) -> None:
        elapsed = time.monotonic() - started
        eta = "?" if frac <= 0.01 else f"{elapsed * (1 - frac) / frac / 3600:.1f}h"
        print(
            f"[{files_seen} files] downloaded {bytes_done / 1e9:.2f} GB, skipped {files_skipped}, "
            f"failed {files_failed}, requests {client.requests}, ETA {eta}",
            flush=True,
        )

    try:
        for src in SOURCES:
            if src.mission not in missions or limit_hit:
                continue
            top = src.root + src.subdir
            # Collection-level files + range directories.
            entries = client.listing(top)
            ranges = [
                e
                for e in entries
                if e[2] and (not args.only_range or args.only_range.lower() in e[0].lower())
            ]
            top_files = [e for e in entries if not e[2] and src.file_re.search(e[0])]
            work: list[tuple[str, int | None]] = [(top + n, s) for n, s, _ in top_files]
            print(f"{src.mission}/{src.subdir}: {len(ranges)} range dirs", flush=True)

            frac_now = [0.0]

            def handle(
                url: str, size: int | None, src: Source = src, frac_now: list[float] = frac_now
            ) -> bool:
                nonlocal files_seen, files_done, files_skipped, files_failed
                nonlocal bytes_done, bytes_listed, limit_hit
                rel = urlparse(url).path[len(urlparse(src.root).path) :]
                dest = OUT_ROOT / src.mission / rel
                files_seen += 1
                bytes_listed += size or 0
                if args.dry_run:
                    return True
                if already_have(dest, size):
                    files_skipped += 1
                    return True
                if args.limit and files_done + files_failed >= args.limit:
                    limit_hit = True
                    return False
                nbytes, status = client.download(url, dest)
                log.writerow([url, nbytes, status, datetime.now(UTC).isoformat(timespec="seconds")])
                log_fh.flush()
                if status == "ok":
                    files_done += 1
                    bytes_done += nbytes
                else:
                    files_failed += 1
                    print(f"  FAILED {url}: {status}", flush=True)
                if files_seen % 100 == 0:
                    progress(frac_now[0])
                return not (args.limit and files_done + files_failed >= args.limit)

            for url, size in work:
                if not handle(url, size):
                    break
            for ri, (rname, _, _) in enumerate(ranges):
                if limit_hit:
                    break
                sols = client.listing(f"{top}{rname}/")
                sol_dirs = [e for e in sols if e[2]]
                direct = [e for e in sols if not e[2] and src.file_re.search(e[0])]
                for n, s, _ in direct:
                    if not handle(f"{top}{rname}/{n}", s):
                        break
                for si, (sname, _, _) in enumerate(sol_dirs):
                    if limit_hit:
                        break
                    frac_now[0] = (ri + si / max(len(sol_dirs), 1)) / len(ranges)
                    for n, s, is_dir in client.listing(f"{top}{rname}/{sname}/"):
                        if is_dir or not src.file_re.search(n):
                            continue
                        if not handle(f"{top}{rname}/{sname}/{n}", s):
                            break
    except KeyboardInterrupt:
        print("\nInterrupted; partial files removed on next run, finished files kept.", flush=True)
    finally:
        log_fh.close()
        for p in OUT_ROOT.rglob("*.part"):
            p.unlink(missing_ok=True)

    label = "DRY RUN" if args.dry_run else "DONE"
    print(
        f"{label}: files seen {files_seen}, listed size {bytes_listed / 1e9:.3f} GB, "
        f"downloaded {files_done} ({bytes_done / 1e9:.3f} GB), skipped {files_skipped}, "
        f"failed {files_failed}, requests {client.requests}, "
        f"{(time.monotonic() - started) / 60:.1f} min",
        flush=True,
    )
    return 1 if files_failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--mission", choices=["meda", "rems", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0, help="stop after N downloaded files")
    ap.add_argument(
        "--dry-run", action="store_true", help="crawl listings only, count files and size"
    )
    ap.add_argument("--only-range", default="", help="substring filter for first-level range dirs")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
