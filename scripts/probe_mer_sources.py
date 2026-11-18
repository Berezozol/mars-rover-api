"""Probe candidate MER (Spirit/Opportunity) image sources with a strict request budget.

Prints a compact summary per URL instead of the page body. Every HTTP request
(including each redirect hop) counts against the budget and is spaced >= 1 s apart.

Usage:
    uv run python scripts/probe_mer_sources.py URL [URL ...] [--budget 10]
        [--counter PATH] [--max-bytes 262144] [--grep REGEX] [--lines N]

--counter keeps a running total across runs in a small text file; the run stops
when the total reaches --total-limit (default 40).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

USER_AGENT = "mars-rover-api-dev"
_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = 1.05 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _fetch(
    client: httpx.Client, url: str, max_bytes: int, state: dict
) -> tuple[httpx.Response, bytes, str]:
    """GET with manual redirects (each hop counted). Reads at most max_bytes of body."""
    hops = 0
    while True:
        if state["used"] >= state["limit"]:
            raise RuntimeError("request budget exhausted")
        _throttle()
        state["used"] += 1
        with client.stream("GET", url) as resp:
            body = b""
            if resp.status_code < 300 or resp.status_code >= 400:
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) >= max_bytes:
                        break
            if resp.is_redirect and hops < 3:
                hops += 1
                nxt = urljoin(url, resp.headers.get("location", ""))
                print(f"  -> {resp.status_code} redirect to {nxt}")
                url = nxt
                continue
            return resp, body, url


def tab_stats(body: bytes) -> None:
    """Summarize rows of a MER edrindex.tab (tab-separated, quoted, 55 columns)."""
    from collections import Counter

    instruments: Counter[str] = Counter()
    ptypes: Counter[str] = Counter()
    sols: list[int] = []
    rows = 0
    for line in body.decode("ascii", errors="replace").splitlines():
        cols = [c.strip().strip('"').strip() for c in line.split("	")]
        if len(cols) < 13 or not cols[11].isdigit():
            continue  # partial first/last line of a sample
        rows += 1
        instruments[cols[3]] += 1
        ptypes[cols[7][11:14]] += 1  # product type, e.g. EFF/EDN/ETH
        sols.append(int(cols[11]))
    print(f"  tab rows parsed={rows} sols={min(sols, default=None)}..{max(sols, default=None)}")
    print(f"  instruments={dict(instruments.most_common())}")
    print(f"  product types={dict(ptypes.most_common())}")


def summarize(url: str, resp: httpx.Response, body: bytes, args: argparse.Namespace) -> None:
    ctype = resp.headers.get("content-type", "?")
    clen = resp.headers.get("content-length", "?")
    print(
        f"  status={resp.status_code} type={ctype} content-length={clen} read={len(body)}"
        f" last-modified={resp.headers.get('last-modified', '-')}"
    )
    if args.tab_stats:
        print(f"  content-range={resp.headers.get('content-range', '-')}")
        tab_stats(body)
        return
    if ctype.startswith(("image/", "application/octet")) and not args.grep:
        print(f"  binary, first bytes={body[:4].hex()}")
        return
    text = body.decode("utf-8", errors="replace").encode("ascii", "replace").decode("ascii")
    if "html" in ctype:
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
        print(f"  title={title.group(1).strip()[:100] if title else '-'}")
        hrefs = re.findall(r'(?:href|src)\s*=\s*["\']([^"\']+)["\']', text, re.I)
        print(f"  links={len(hrefs)}")
        pat = re.compile(args.grep, re.I) if args.grep else None
        shown = [h for h in hrefs if (pat.search(h) if pat else True)]
        if pat:
            print(f"  links matching /{args.grep}/: {len(shown)}")
        for h in shown[: args.lines]:
            print(f"    {h[:160]}")
    else:
        lines = text.splitlines()
        print(f"  lines-read={len(lines)}")
        pat = re.compile(args.grep, re.I) if args.grep else None
        sel = [ln for ln in lines if (pat.search(ln) if pat else True)]
        for ln in sel[: args.lines]:
            print(f"    {ln[:240]}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("urls", nargs="+")
    p.add_argument("--budget", type=int, default=10, help="max requests for this run")
    p.add_argument("--counter", type=Path, help="file with running total across runs")
    p.add_argument("--total-limit", type=int, default=40)
    p.add_argument("--max-bytes", type=int, default=262144)
    p.add_argument("--grep", help="regex to filter shown links/lines")
    p.add_argument("--lines", type=int, default=8)
    p.add_argument(
        "--range-start",
        type=int,
        help="send Range: bytes=N-(N+max-bytes) to sample the middle of a big file",
    )
    p.add_argument(
        "--tab-stats",
        action="store_true",
        help="treat body as a MER PDS edrindex.tab sample: count instruments, product types, sols",
    )
    args = p.parse_args()

    total_before = (
        int(args.counter.read_text().strip()) if args.counter and args.counter.exists() else 0
    )
    limit = min(args.budget, args.total_limit - total_before)
    state = {"used": 0, "limit": max(limit, 0)}
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if args.range_start is not None:
        headers["Range"] = f"bytes={args.range_start}-{args.range_start + args.max_bytes - 1}"
    try:
        _run(args, headers, state)
    finally:
        total = total_before + state["used"]
        if args.counter:
            args.counter.write_text(str(total))
        print(f"requests this run={state['used']} running total={total}/{args.total_limit}")
    return 0


def _run(args: argparse.Namespace, headers: dict, state: dict) -> None:
    with httpx.Client(headers=headers, timeout=60.0, follow_redirects=False) as client:
        for url in args.urls:
            print(f"== {url}")
            try:
                resp, body, final = _fetch(client, url, args.max_bytes, state)
            except RuntimeError as exc:
                print(f"  STOP: {exc}")
                break
            except httpx.HTTPError as exc:
                print(f"  error: {type(exc).__name__}: {exc}")
                continue
            summarize(final, resp, body, args)


if __name__ == "__main__":
    sys.exit(main())
