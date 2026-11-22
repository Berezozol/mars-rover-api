"""Stage 19, AC-16: `docs/openapi.json` shows `mast` and the `fields` parameter."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
LIST_PATHS = [
    "/api/v2/photos",
    "/api/v2/rovers/{rover}/photos",
    "/api/v2/rovers/{rover}/photos/latest",
    "/api/v2/photos/batch",
]
DETAIL_PATH = re.compile(r"^/api/v2/photos/\{[a-z_]+\}$")
# Required keys of Photo before stage 19 (`mast` is added to them, never made optional).
OLD_REQUIRED = [
    "id",
    "rover",
    "sol",
    "earth_date",
    "taken_at",
    "camera",
    "sample_type",
    "title",
    "img_src",
    "images",
    "credit",
    "source",
    "width",
    "height",
    "local_time",
]


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = schema["components"]["schemas"][node["$ref"].rsplit("/", 1)[1]]
    return node


def response_schema(schema: dict[str, Any], path: str) -> dict[str, Any]:
    ok = schema["paths"][path]["get"]["responses"]["200"]
    return resolve(schema, ok["content"]["application/json"]["schema"])


def photo_schema(schema: dict[str, Any], path: str) -> dict[str, Any]:
    return resolve(schema, response_schema(schema, path)["properties"]["items"]["items"])


def detail_path(schema: dict[str, Any]) -> str:
    return next(p for p in schema["paths"] if DETAIL_PATH.match(p) and p != "/api/v2/photos/batch")


def detail_schema(schema: dict[str, Any]) -> dict[str, Any]:
    body = response_schema(schema, detail_path(schema))
    # The card may be wrapped (`photo` root) or bare.
    if "photo" in body.get("properties", {}):
        return resolve(schema, body["properties"]["photo"])
    return body


def allows_null(prop: dict[str, Any]) -> bool:
    options = prop.get("anyOf") or prop.get("oneOf") or []
    return any(o.get("type") == "null" for o in options)


def ref_name(prop: dict[str, Any]) -> str | None:
    for option in prop.get("anyOf") or prop.get("oneOf") or []:
        if "$ref" in option:
            return option["$ref"].rsplit("/", 1)[1]
    return None


@pytest.mark.parametrize("path", LIST_PATHS)
def test_ac16_photo_items_have_a_required_nullable_mast_object(
    schema: dict[str, Any], path: str
) -> None:
    photo = photo_schema(schema, path)

    assert "mast" in photo["properties"]
    assert "mast" in photo["required"]
    prop = photo["properties"]["mast"]
    assert allows_null(prop)
    assert ref_name(prop) == "MastOut"
    mast = schema["components"]["schemas"]["MastOut"]
    assert list(mast["properties"]) == ["azimuth", "elevation"]
    assert sorted(mast["required"]) == ["azimuth", "elevation"]


def test_ac16_photo_required_list_keeps_the_old_keys_plus_mast(schema: dict[str, Any]) -> None:
    required = photo_schema(schema, "/api/v2/photos")["required"]

    assert [k for k in required if k != "mast"] == OLD_REQUIRED
    assert "near_stop" not in required


def test_ac16_photo_detail_has_a_required_nullable_mast_object(schema: dict[str, Any]) -> None:
    detail = detail_schema(schema)

    assert "mast" in detail["properties"]
    assert "mast" in detail["required"]
    assert allows_null(detail["properties"]["mast"])
    assert ref_name(detail["properties"]["mast"]) == "MastOut"


@pytest.mark.parametrize("path", LIST_PATHS)
def test_ac16_list_endpoints_declare_the_fields_parameter(
    schema: dict[str, Any], path: str
) -> None:
    params = {p["name"]: p for p in schema["paths"][path]["get"]["parameters"]}

    assert "fields" in params
    field = params["fields"]
    assert field["in"] == "query"
    assert not field.get("required", False)
    inner = field["schema"]
    if "anyOf" in inner:
        inner = next(o for o in inner["anyOf"] if o.get("type") == "string")
    assert inner["type"] == "string"
    assert inner["maxLength"] == 300
    assert field.get("description")


def test_ac16_detail_endpoint_does_not_declare_fields(schema: dict[str, Any]) -> None:
    names = [p["name"] for p in schema["paths"][detail_path(schema)]["get"].get("parameters", [])]

    assert "fields" not in names
