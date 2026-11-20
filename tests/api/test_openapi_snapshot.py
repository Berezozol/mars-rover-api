"""docs/openapi.json in the repository must equal the schema the application generates."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

SNAPSHOT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def test_docs_openapi_json_matches_the_application_schema(app: FastAPI) -> None:
    assert SNAPSHOT.is_file(), (
        "docs/openapi.json is missing; generate it with: "
        "uv run python -m app.cli openapi --output docs/openapi.json"
    )
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(app.openapi()))

    assert committed == current, (
        "docs/openapi.json is stale (the API changed). Regenerate it with: "
        "uv run python -m app.cli openapi --output docs/openapi.json"
    )
