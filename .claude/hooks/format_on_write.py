"""PostToolUse hook (Edit|Write): format a just-written file with the project's formatter.

Python files go through ``ruff format``; ``frontend/`` sources through the frontend's own
prettier (only when its node_modules exist). Never blocks: formatter errors are ignored
and the result is not reported to the model, so the hook costs no tokens.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

PRETTIER_SUFFIXES = {".ts", ".tsx", ".js", ".css", ".json", ".html"}


def run(command: list[str], cwd: Path) -> None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(command, cwd=cwd, capture_output=True, timeout=60, check=False)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        path = Path((payload.get("tool_input") or {}).get("file_path") or "")
        root = Path(payload.get("cwd") or ".")
    except ValueError:
        return 0
    if not path.is_file():
        return 0
    parts = {p.lower() for p in path.parts}
    if {"node_modules", "dist", "migrations", ".venv"} & parts:
        return 0
    if path.suffix == ".py":
        uv = shutil.which("uv")
        if uv:
            run([uv, "run", "--quiet", "ruff", "format", str(path)], root)
    elif path.suffix in PRETTIER_SUFFIXES and "frontend" in parts:
        frontend = root / "frontend"
        npx = shutil.which("npx")
        if npx and (frontend / "node_modules").is_dir():
            run([npx, "--no-install", "prettier", "--write", str(path)], frontend)
    return 0


if __name__ == "__main__":
    sys.exit(main())
