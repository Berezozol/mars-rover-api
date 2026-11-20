"""Sum token usage of sub-agent runs per role from Claude Code session transcripts.

Reads ``<projects-dir>/*/<session>/subagents/agent-*.jsonl`` (read-only), detects the
role from the first prompt of each run and prints a table: runs, turns, output,
cache creation and cache read tokens, plus each role's share. Cache read is cheap
and dominates the raw total, so shares are shown for output + cache creation.

    uv run --no-project python .claude/skills/usage-by-role/usage_by_role.py \
        [--since 2026-11-17] [--match "Mars Rover API"] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROLES = (
    "backend-dev",
    "qa-engineer",
    "reviewer",
    "security-reviewer",
    "devops",
    "data-engineer",
    "architect",
    "designer",
    "frontend-dev",
    "frontend-qa",
    "tech-writer",
)
ZONE_HINTS = (
    ("frontend/tests", "frontend-qa"),
    ("zone: design/", "designer"),
    ("frontend/", "frontend-dev"),
)
ROLE_RE = re.compile(
    r"(?:project's |role `|You are (?:the )?|as the |Act as the )\s*`?(" + "|".join(ROLES) + ")"
)
ANY_ROLE_RE = re.compile("(" + "|".join(ROLES) + ")")


def first_record(path: Path) -> dict | None:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


def prompt_text(record: dict) -> str:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


def detect_role(prompt: str) -> str:
    head = prompt[:400]
    found = ROLE_RE.search(head) or ANY_ROLE_RE.search(head)
    if found:
        return found.group(1)
    # Prompts that name a zone instead of a role.
    for marker, role in ZONE_HINTS:
        if marker in prompt[:600]:
            return role
    return "unknown"


def totals(path: Path) -> tuple[int, int, int, int]:
    turns = output = created = read = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                message = json.loads(line).get("message") or {}
            except ValueError:
                continue
            usage = message.get("usage")
            if message.get("role") == "assistant" and usage:
                turns += 1
                output += usage.get("output_tokens", 0)
                created += usage.get("cache_creation_input_tokens", 0)
                read += usage.get("cache_read_input_tokens", 0)
    return turns, output, created, read


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--projects-dir", default=str(Path.home() / ".claude" / "projects"))
    parser.add_argument(
        "--since", default="", help="ISO date; runs that started earlier are skipped"
    )
    parser.add_argument(
        "--match", default="Mars Rover API", help="only runs whose first prompt contains this text"
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()

    table: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    for path in Path(args.projects_dir).glob("*/*/subagents/agent-*.jsonl"):
        record = first_record(path)
        if not record:
            continue
        prompt = prompt_text(record)
        if args.match and args.match not in prompt:
            continue
        if args.since and str(record.get("timestamp", ""))[:10] < args.since:
            continue
        turns, output, created, read = totals(path)
        row = table[detect_role(prompt)]
        row[0] += 1
        row[1] += turns
        row[2] += output
        row[3] += created
        row[4] += read

    if not table:
        print("no sub-agent transcripts found", file=sys.stderr)
        return 1
    grand_new = sum(row[2] + row[3] for row in table.values()) or 1
    ordered = sorted(table.items(), key=lambda item: -(item[1][2] + item[1][3]))
    if args.json:
        print(
            json.dumps(
                {
                    role: dict(
                        zip(
                            ("runs", "turns", "output", "cache_creation", "cache_read"),
                            row,
                            strict=True,
                        )
                    )
                    for role, row in ordered
                },
                indent=2,
            )
        )
        return 0
    print(
        f"{'role':<18}{'runs':>5}{'turns':>7}{'output':>11}{'cache_new':>12}{'cache_read':>14}{'share':>8}"
    )
    for role, row in ordered:
        share = 100 * (row[2] + row[3]) / grand_new
        print(
            f"{role:<18}{row[0]:>5}{row[1]:>7}{row[2]:>11,}{row[3]:>12,}{row[4]:>14,}{share:>7.1f}%"
        )
    total = [sum(row[i] for row in table.values()) for i in range(5)]
    print(f"{'total':<18}{total[0]:>5}{total[1]:>7}{total[2]:>11,}{total[3]:>12,}{total[4]:>14,}")
    print(
        "share = (output + cache_new) of all runs; cache_read is cheap and not counted in the share"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
