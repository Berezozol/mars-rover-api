"""docs/openapi.json in the repository must equal the schema the CLI generates.

The schema is built by the CLI command itself (`app.cli.main`), so the settings it switches on
(keys and sign-up with placeholder values) are not copied into the test. The base settings are
fixed (no `.env`, no environment of the machine), and the engine the application creates is
closed.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

import app.cli
import app.main
from app.core.config import Settings

pytestmark = pytest.mark.api

SNAPSHOT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


async def test_docs_openapi_json_matches_the_cli_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert SNAPSHOT.is_file(), (
        "docs/openapi.json is missing; generate it with: "
        "uv run python -m app.cli openapi --output docs/openapi.json"
    )
    monkeypatch.setattr(app.cli, "get_settings", lambda: Settings(_env_file=None))
    built: list[FastAPI] = []
    real_create_app = app.main.create_app

    def capture(*args: Any, **kwargs: Any) -> FastAPI:
        application = real_create_app(*args, **kwargs)
        built.append(application)
        return application

    monkeypatch.setattr(app.main, "create_app", capture)
    generated = tmp_path / "openapi.json"

    try:
        code = app.cli.main(["openapi", "--output", str(generated)])
    finally:
        for application in built:
            engine = getattr(application.state, "engine", None)
            if engine is not None:
                await engine.dispose()

    assert code == 0
    assert json.loads(SNAPSHOT.read_text(encoding="utf-8")) == json.loads(
        generated.read_text(encoding="utf-8")
    ), (
        "docs/openapi.json is stale (the API changed). Regenerate it with: "
        "uv run python -m app.cli openapi --output docs/openapi.json"
    )
