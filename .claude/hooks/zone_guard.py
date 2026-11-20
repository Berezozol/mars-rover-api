"""PreToolUse hook (Edit|Write): keep sub-agents inside their file zones from CLAUDE.md.

The hook payload carries ``agent_type`` only for tool calls made by a sub-agent; calls from
the lead session (no ``agent_type``) and from roles without a zone list are always allowed.
Fails open: any parsing problem allows the call. A violation is denied with a reason that
tells the agent to describe the needed change in its report instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath

# Path prefixes (relative to the project root, forward slashes) each role may write to.
ZONES: dict[str, tuple[str, ...]] = {
    "backend-dev": (
        "app/",
        "migrations/",
        "pyproject.toml",
        "uv.lock",
        "docker-compose.yml",
        ".env.example",
        "alembic.ini",
    ),
    "qa-engineer": ("tests/", "scripts/record_fixtures.py"),
    "architect": ("docs/ARCHITECTURE.md", "docs/proposals/"),
    "data-engineer": ("docs/DATA-SOURCES.md", "docs/reports/", "scripts/"),
    "designer": ("design/",),
    "devops": ("Dockerfile", ".dockerignore", ".github/", "deploy/"),
    "frontend-dev": ("frontend/",),
    "frontend-qa": ("frontend/tests/", "frontend/e2e/"),
    "tech-writer": ("README.md", "examples/"),
}
# Paths a role must not write even though a broader prefix of its zone covers them.
EXCLUDED: dict[str, tuple[str, ...]] = {
    "frontend-dev": ("frontend/tests/", "frontend/e2e/"),
    "data-engineer": ("scripts/record_fixtures.py",),
}


def relative(path: str, root: str) -> str | None:
    """Path relative to the project root with forward slashes, or None if outside it."""
    normal = path.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/") + "/"
    if normal.lower().startswith(base.lower()):
        normal = normal[len(base) :]
    elif PurePosixPath(normal).is_absolute() or (len(normal) > 1 and normal[1] == ":"):
        return None
    return normal.lstrip("./") if normal.startswith("./") else normal


def allowed(role: str, rel: str) -> bool:
    if any(rel == p or rel.startswith(p) for p in EXCLUDED.get(role, ())):
        return False
    return any(rel == p or rel.startswith(p) for p in ZONES[role])


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        role = payload.get("agent_type") or ""
        path = (payload.get("tool_input") or {}).get("file_path") or ""
        root = payload.get("cwd") or ""
    except ValueError:
        return 0
    if role not in ZONES or not path:
        return 0
    rel = relative(path, root)
    if rel is None:
        return 0  # outside the project (scratchpad, notes): not this hook's business
    if allowed(role, rel):
        return 0
    zone = ", ".join(ZONES[role])
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{role} may write only: {zone}. '{rel}' belongs to another role "
                    "(see CLAUDE.md). Describe the change you need in your report instead."
                ),
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
