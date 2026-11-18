"""Budgeted fetches from the PDS Imaging Node for the MER (Spirit/Opportunity) import.

Every HTTP request counts against a running total kept in data/mer/requests.count
(hard cap --total-limit, default 60), is spaced >= 1 s apart, and is sent with
User-Agent ``mars-rover-api-dev``. Only NASA/JPL PDS hosts are allowed. Output is a
compact summary, never a page body.

Subcommands:
    download VOLUME [VOLUME ...] [--deadline SEC]
        Stream index/edrindex.lbl + edrindex.tab of each volume into
        data/mer/<volume>/index/ via a .part file (resumed with Range if present),
        rename only when the byte count equals Content-Length, then check that the
        number of CRLF rows equals ROWS from the label. NB: ROWS * ROW_BYTES does NOT
        match the real files (rows are variable-width; see docs/DATA-SOURCES.md).
        Files already complete (row count == ROWS) are skipped without a request.
    head URL [URL ...]
        HEAD each URL, print status / content-type / content-length.
    list URL [--grep REGEX] [--lines N]
        GET an HTML directory listing, print matching hrefs.
    text URL [--grep REGEX] [--lines N] [--context N] [--max-bytes N]
        GET a text document, print lines matching REGEX (with context).

Usage: uv run python scripts/mer_fetch.py <subcommand> ...
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

USER_AGENT = "mars-rover-api-dev"
BASE = "https://planetarydata.jpl.nasa.gov/img/data/mer/"
ROOT = Path(__file__).resolve().parent.parent / "data" / "mer"
COUNTER = ROOT / "requests.count"
ALLOWED_HOSTS = ("planetarydata.jpl.nasa.gov", "pds-imaging.jpl.nasa.gov", "pds.nasa.gov")

_last = 0.0


class Budget:
    def __init__(self, limit: int) -> None:
        ROOT.mkdir(parents=True, exist_ok=True)
        self.before = int(COUNTER.read_text().strip()) if COUNTER.exists() else 0
        self.used = 0
        self.limit = limit

    def take(self, url: str) -> None:
        global _last
        host = urlparse(url).hostname or ""
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
            raise RuntimeError(f"host not allowed: {host}")
        if self.before + self.used >= self.limit:
            raise RuntimeError("request budget exhausted")
        wait = 1.05 - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        self.used += 1
        COUNTER.write_text(str(self.before + self.used))

    def report(self) -> None:
        print(f"requests this run={self.used} running total={self.before + self.used}/{self.limit}")


def parse_label(text: str) -> dict[str, int]:
    out = {}
    for key in ("ROWS", "ROW_BYTES", "COLUMNS"):
        m = re.search(rf"^\s*{key}\s*=\s*(\d+)", text, re.M)
        if m:
            out[key] = int(m.group(1))
    return out


def _rows(path: Path) -> int:
    n = 0
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 24):
            n += chunk.count(b"\r\n")
    return n


def _stream_to(client: httpx.Client, budget: Budget, url: str, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    budget.take(url)
    t0 = time.monotonic()
    with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code == 200 and have:
            have = 0  # server ignored Range: restart
        elif resp.status_code not in (200, 206):
            raise RuntimeError(f"{url}: HTTP {resp.status_code}")
        total = resp.headers.get("content-length", "?")
        with part.open("ab" if have else "wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
    size = part.stat().st_size
    if total != "?" and size != have + int(total):
        raise RuntimeError(f"{url}: got {size} B, expected {have + int(total)}; .part kept")
    part.replace(dest)
    dt = time.monotonic() - t0
    print(
        f"  {dest.name}: {size:,} B (content-length {total}, resumed from {have:,}) "
        f"in {dt:.0f}s = {size / max(dt, 0.001) / 1e6:.1f} MB/s"
    )


def cmd_download(args: argparse.Namespace, client: httpx.Client, budget: Budget) -> None:
    start = time.monotonic()
    for vol in args.volumes:
        if time.monotonic() - start > args.deadline:
            print(f"deadline reached, stopping before {vol}")
            break
        d = ROOT / vol / "index"
        d.mkdir(parents=True, exist_ok=True)
        lbl, tab = d / "edrindex.lbl", d / "edrindex.tab"
        print(f"== {vol}")
        if not lbl.exists():
            _stream_to(client, budget, f"{BASE}{vol}/index/edrindex.lbl", lbl)
        meta = parse_label(lbl.read_text(encoding="ascii", errors="replace"))
        print(f"  label {meta} (ROWS*ROW_BYTES={meta.get('ROWS', 0) * meta.get('ROW_BYTES', 0):,})")
        if tab.exists() and _rows(tab) == meta.get("ROWS"):
            print(f"  tab complete ({tab.stat().st_size:,} B), skipped")
            continue
        if tab.exists():
            print(f"  tab rows {_rows(tab):,} != ROWS; re-downloading")
            tab.unlink()
        _stream_to(client, budget, f"{BASE}{vol}/index/edrindex.tab", tab)
        n = _rows(tab)
        print(
            f"  rows={n:,} label ROWS={meta.get('ROWS')}: "
            f"{'OK' if n == meta.get('ROWS') else 'MISMATCH'}"
        )


def cmd_head(args: argparse.Namespace, client: httpx.Client, budget: Budget) -> None:
    for url in args.urls:
        budget.take(url)
        try:
            r = client.head(url)
        except httpx.HTTPError as exc:
            print(f"ERR {type(exc).__name__} {url}")
            continue
        print(
            f"{r.status_code} {r.headers.get('content-type', '-'):<12} "
            f"{r.headers.get('content-length', '-'):>7} {url.removeprefix(BASE)}"
        )


def _get_text(client: httpx.Client, budget: Budget, url: str, max_bytes: int) -> tuple[int, str]:
    budget.take(url)
    body = b""
    with client.stream("GET", url) as r:
        for chunk in r.iter_bytes():
            body += chunk
            if len(body) >= max_bytes:
                break
        print(f"status={r.status_code} type={r.headers.get('content-type', '-')} read={len(body)}")
    return r.status_code, body.decode("utf-8", errors="replace")


def cmd_list(args: argparse.Namespace, client: httpx.Client, budget: Budget) -> None:
    _, text = _get_text(client, budget, args.url, args.max_bytes)
    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', text, re.I)
    pat = re.compile(args.grep, re.I) if args.grep else None
    sel = [h for h in hrefs if not pat or pat.search(h)]
    print(f"links={len(hrefs)} matching={len(sel)}")
    for h in sel[: args.lines]:
        print(f"  {h[:160]}")


def cmd_text(args: argparse.Namespace, client: httpx.Client, budget: Budget) -> None:
    _, text = _get_text(client, budget, args.url, args.max_bytes)
    lines = text.splitlines()
    print(f"lines={len(lines)}")
    pat = re.compile(args.grep, re.I)
    shown = 0
    for i, ln in enumerate(lines):
        if pat.search(ln):
            lo, hi = max(0, i - args.context), min(len(lines), i + args.context + 1)
            for j in range(lo, hi):
                print(f"  {j:5d}: {lines[j][:200]}")
            print("  --")
            shown += 1
            if shown >= args.lines:
                break


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--total-limit", type=int, default=60)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("volumes", nargs="+")
    d.add_argument(
        "--deadline", type=float, default=420.0, help="don't start new volumes after N s"
    )
    h = sub.add_parser("head")
    h.add_argument("urls", nargs="+")
    for name in ("list", "text"):
        s = sub.add_parser(name)
        s.add_argument("url")
        s.add_argument("--grep", default="." if name == "text" else None)
        s.add_argument("--lines", type=int, default=20)
        s.add_argument("--context", type=int, default=0)
        s.add_argument("--max-bytes", type=int, default=1 << 20)
    args = p.parse_args()
    budget = Budget(args.total_limit)
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    try:
        with httpx.Client(headers=headers, timeout=120.0, follow_redirects=False) as client:
            {"download": cmd_download, "head": cmd_head, "list": cmd_list, "text": cmd_text}[
                args.cmd
            ](args, client, budget)
    except RuntimeError as exc:
        print(f"STOP: {exc}")
    finally:
        budget.report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
