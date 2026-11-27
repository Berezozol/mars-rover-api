"""Flags pull requests that need the owner's decision before an automatic merge.

Reasons (any one is enough):
  * a migration in migrations/versions/ is added with a destructive operation in upgrade():
    drop_column, drop_table, drop_constraint (any drop_* except drop_index), renames
    (rename_table, alter_column(new_column_name=...), RENAME in op.execute), alter_column with
    type_= or nullable=False, ALTER ... TYPE / SET NOT NULL / DROP in op.execute SQL;
  * an existing migration is modified or deleted (applied migrations are immutable);
  * docs/API.md or docs/LEGACY-API.md (the API contracts) is changed.
downgrade() is not scanned: it legitimately drops what upgrade() added.

Standard library only, no network. Exit code: 0 nothing found, 1 reasons found (printed one per
line as "- <reason>", and as Markdown in --markdown FILE), 2 usage or input error.

Usage (CI):   python .github/scripts/migration_guard.py --base <sha> --head <sha> [--markdown out.md]
Usage (test): python .github/scripts/migration_guard.py --status-file changes.txt --root DIR
  changes.txt: one change per line, "<A|M|D> <path>", paths relative to DIR.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

MIGRATIONS_DIR = "migrations/versions/"
CONTRACT_FILES = {"docs/API.md", "docs/LEGACY-API.md"}
RISKY_SQL = ("DROP ", "RENAME ", " TYPE ", "SET NOT NULL", "TRUNCATE ")
SAFE_DROPS = {"drop_index"}


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _call_reasons(call: ast.Call) -> list[str]:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    out: list[str] = []
    if name.startswith("drop_") and name not in SAFE_DROPS:
        out.append(f"{name}() at line {call.lineno}")
    elif name == "rename_table":
        out.append(f"rename_table() at line {call.lineno}")
    elif name == "alter_column":
        if _kw(call, "new_column_name") is not None:
            out.append(f"alter_column(new_column_name=...) (rename) at line {call.lineno}")
        if _kw(call, "type_") is not None:
            out.append(f"alter_column(type_=...) at line {call.lineno}")
        nullable = _kw(call, "nullable")
        if isinstance(nullable, ast.Constant) and nullable.value is False:
            out.append(f"alter_column(nullable=False) at line {call.lineno}")
    elif name == "execute" and call.args:
        for node in ast.walk(call.args[0]):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                sql = f" {node.value.upper()} "
                hit = next((r for r in RISKY_SQL if r in sql), None)
                if hit:
                    out.append(f"op.execute with '{hit.strip()}' at line {call.lineno}")
                    break
    return out


def scan_upgrade(source: str) -> list[str]:
    """Dangerous operations inside upgrade() of a migration file."""
    tree = ast.parse(source)
    reasons: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    reasons.extend(_call_reasons(sub))
    return reasons


def analyze(changes: list[tuple[str, str]], read: Callable[[str], str]) -> list[str]:
    """changes: (status, path) with status A, M or D. read(path) returns the new file text."""
    reasons: list[str] = []
    for status, path in changes:
        if path in CONTRACT_FILES:
            reasons.append(f"{path} changed: the API contract needs the owner's approval")
        if not (path.startswith(MIGRATIONS_DIR) and path.endswith(".py")):
            continue
        if path.endswith("/__init__.py"):
            continue
        if status in ("M", "D"):
            word = "modified" if status == "M" else "deleted"
            reasons.append(f"{path}: existing migration {word} (applied migrations are immutable)")
        if status in ("A", "M"):
            try:
                found = scan_upgrade(read(path))
            except SyntaxError as exc:
                found = [f"cannot parse ({exc.msg}, line {exc.lineno})"]
            reasons.extend(f"{path}: {r}" for r in found)
    return reasons


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _git_changes(base: str, head: str) -> list[tuple[str, str]]:
    out = _git("diff", "--name-status", "--no-renames", f"{base}...{head}")
    changes = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        if status and path:
            changes.append((status[0], path))
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", help="base commit (git mode)")
    parser.add_argument("--head", help="head commit (git mode)")
    parser.add_argument("--status-file", help="list of '<A|M|D> <path>' lines (file mode)")
    parser.add_argument("--root", default=".", help="directory with the files (file mode)")
    parser.add_argument("--markdown", help="write the PR comment text here when reasons exist")
    args = parser.parse_args(argv)

    try:
        if args.status_file:
            root = Path(args.root)
            changes = []
            for line in Path(args.status_file).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    status, path = line.split(None, 1)
                    changes.append((status.upper()[0], path.strip()))

            def read(path: str) -> str:
                return (root / path).read_text(encoding="utf-8")

        elif args.base and args.head:
            changes = _git_changes(args.base, args.head)

            def read(path: str) -> str:
                return _git("show", f"{args.head}:{path}")

        else:
            parser.error("give --base and --head, or --status-file")
            return 2
        reasons = analyze(changes, read)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"migration_guard: {exc}", file=sys.stderr)
        return 2

    for reason in reasons:
        print(f"- {reason}")
    if reasons and args.markdown:
        body = "\n".join(f"- {r}" for r in reasons)
        Path(args.markdown).write_text(
            "**Label `needs-owner` set: this PR will not merge automatically.**\n\n"
            f"{body}\n\nThe owner reviews it; removing the label re-enables auto-merge "
            "(a new push runs the check again).\n",
            encoding="utf-8",
        )
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
