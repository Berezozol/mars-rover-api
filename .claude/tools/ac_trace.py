"""Check that every acceptance criterion (AC-N) of a spec is mentioned in the tests.

    uv run --no-project python .claude/tools/ac_trace.py <spec.md> [--tests tests frontend/tests]

Criteria are the ``AC-<number>`` tokens in the spec's acceptance-criteria table; a test
covers a criterion when its file mentions the same token (name, docstring or comment).
Exit code 1 when a criterion is not covered. Read-only, no network.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

AC_RE = re.compile(r"\bAC-(\d+)\b")
TEST_SUFFIXES = {".py", ".ts", ".tsx", ".js"}
SKIP_DIRS = {"node_modules", "dist", "__pycache__", ".venv"}


def spec_criteria(spec: Path) -> list[str]:
    """AC ids from table rows of the spec (lines starting with ``| AC-``)."""
    found: list[str] = []
    for line in spec.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\|\s*(AC-\d+)\s*\|", line)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def covered(test_dirs: list[Path]) -> dict[str, list[str]]:
    """Map AC id -> test files that mention it."""
    result: dict[str, list[str]] = {}
    for root in test_dirs:
        for path in root.rglob("*"):
            if path.suffix not in TEST_SUFFIXES or SKIP_DIRS & set(path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for number in set(AC_RE.findall(text)):
                result.setdefault(f"AC-{number}", []).append(str(path))
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path)
    parser.add_argument(
        "--tests", nargs="+", type=Path, default=[Path("tests"), Path("frontend/tests")]
    )
    args = parser.parse_args()
    criteria = spec_criteria(args.spec)
    if not criteria:
        print(f"{args.spec}: no AC-N rows found in the acceptance-criteria table", file=sys.stderr)
        return 1
    mentions = covered([p for p in args.tests if p.exists()])
    missing = [ac for ac in criteria if ac not in mentions]
    for ac in criteria:
        files = mentions.get(ac, [])
        print(f"{ac:<8}{'OK ' if files else 'MISSING'}  {', '.join(sorted(files)[:3])}")
    print(f"{len(criteria) - len(missing)}/{len(criteria)} criteria covered")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
