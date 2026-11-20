"""Lint a spec written from docs/proposals/_TEMPLATE.md.

    uv run --no-project python .claude/tools/spec_lint.py docs/proposals/<spec>.md

Checks: goal/scope section, numbered acceptance criteria with a "who checks" column,
role assignments, external requests list, owner questions, no leftover template
placeholders (``<...>``). Exit code 1 on any problem. Read-only, no network.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED = (
    ("goal and scope", r"\*\*Входит:\*\*"),
    ("'not included' line", r"\*\*Не входит:\*\*"),
    ("done criterion", r"\*\*Готово, когда:\*\*"),
    ("acceptance criteria section", r"^##\s+2\.\s+Критерии приёмки"),
    ("role assignments section", r"^##\s+\d+\.\s+Задания ролям"),
    ("external requests section", r"^##\s+\d+\.\s+Внешние запросы"),
    ("owner questions section", r"^##\s+\d+\.\s+Вопросы владельцу"),
)


def lint(text: str) -> list[str]:
    problems = [
        f"missing {name}" for name, pattern in REQUIRED if not re.search(pattern, text, re.M)
    ]
    rows = re.findall(r"^\|\s*(AC-\d+)\s*\|(.*)$", text, re.M)
    if not rows:
        problems.append("no AC-N rows in the acceptance-criteria table")
    numbers = [int(ac.split("-")[1]) for ac, _ in rows]
    if numbers != sorted(set(numbers)) or (numbers and numbers[0] != 1):
        problems.append(f"AC numbers must be unique and ascending from AC-1, got {numbers}")
    for ac, rest in rows:
        cells = [cell.strip() for cell in rest.strip().strip("|").split("|")]
        if len(cells) < 3 or not all(cells[:3]):
            problems.append(f"{ac}: needs criterion, who checks and test layer")
        elif "→" not in cells[0] and "->" not in cells[0]:
            problems.append(f"{ac}: write the criterion as 'input → result'")
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    placeholders = re.findall(r"<[^<>\n]*[А-Яа-яЁё][^<>\n]*>|<(?:NNNN|rover)>", body)
    if placeholders:
        problems.append(f"template placeholders left: {', '.join(placeholders[:5])}")
    return problems


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    problems = lint(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for problem in problems:
        print(f"- {problem}")
    print("spec OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
