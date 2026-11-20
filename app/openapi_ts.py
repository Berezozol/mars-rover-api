"""TypeScript declarations from the OpenAPI ``components/schemas`` (stdlib only).

Mapping: object -> ``export interface`` (not required -> ``?``), ``anyOf`` with ``null`` ->
``T | null``, enum/const -> string-literal unions, ``$ref`` -> type name, arrays -> ``T[]``,
``additionalProperties`` -> ``Record<string, T>``, anything else -> ``unknown``.
Output is deterministic: schemas and properties are sorted, newline is ``\\n``.
"""

import json
from typing import Any

HEADER = (
    "// Generated from openapi.json - do not edit; regenerate with\n"
    "// `uv run python -m app.cli openapi-ts --output examples/types.ts`\n"
)
_PRIMITIVES = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean"}
_IDENT_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")


def _type_name(name: str) -> str:
    cleaned = "".join(ch if ch in _IDENT_OK else "_" for ch in name)
    return f"_{cleaned}" if cleaned[:1].isdigit() else cleaned


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if value is not None else "null"


def _property_key(name: str) -> str:
    plain = name and not name[0].isdigit() and all(ch in _IDENT_OK for ch in name)
    return name if plain else json.dumps(name, ensure_ascii=False)


def _union(parts: list[str]) -> str:
    unique = sorted(set(parts), key=lambda p: (p == "null", p))
    return " | ".join(unique) if unique else "unknown"


def ts_type(schema: dict[str, Any]) -> str:
    """TypeScript type expression of one schema node."""
    if "$ref" in schema:
        return _type_name(str(schema["$ref"]).rsplit("/", 1)[-1])
    if "enum" in schema:
        return _union([_literal(v) for v in schema["enum"]])
    if "const" in schema:
        return _literal(schema["const"])
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return _union([_wrap(ts_type(s)) for s in schema[key]])
    if "allOf" in schema:
        return " & ".join(_wrap(ts_type(s)) for s in schema["allOf"]) or "unknown"
    kind = schema.get("type")
    if isinstance(kind, list):
        return _union([ts_type({**schema, "type": k}) for k in kind])
    if kind == "null":
        return "null"
    if kind in _PRIMITIVES:
        return _PRIMITIVES[kind]
    if kind == "array":
        return f"{_wrap(ts_type(schema['items']))}[]" if "items" in schema else "unknown[]"
    if kind == "object" or "properties" in schema or "additionalProperties" in schema:
        return _object_type(schema)
    return "unknown"


def _wrap(expression: str) -> str:
    return f"({expression})" if " | " in expression or " & " in expression else expression


def _object_type(schema: dict[str, Any]) -> str:
    if "properties" in schema:
        return _inline_object(schema)
    extra = schema.get("additionalProperties")
    if isinstance(extra, dict):
        return f"Record<string, {ts_type(extra)}>"
    return "Record<string, unknown>"


def _members(schema: dict[str, Any], indent: str) -> list[str]:
    required = set(schema.get("required", []))
    lines = []
    for name in sorted(schema.get("properties", {})):
        optional = "" if name in required else "?"
        value = ts_type(schema["properties"][name])
        lines.append(f"{indent}{_property_key(name)}{optional}: {value};")
    return lines


def _inline_object(schema: dict[str, Any]) -> str:
    lines = _members(schema, "  ")
    return "{\n" + "\n".join(lines) + "\n}" if lines else "Record<string, unknown>"


def _declaration(name: str, schema: dict[str, Any]) -> str:
    ident = _type_name(name)
    if "properties" in schema and "enum" not in schema:
        body = "\n".join(_members(schema, "  "))
        return f"export interface {ident} {{\n{body}\n}}\n" if body else _alias(ident, schema)
    return _alias(ident, schema)


def _alias(ident: str, schema: dict[str, Any]) -> str:
    return f"export type {ident} = {ts_type(schema)};\n"


def generate(openapi: dict[str, Any]) -> str:
    """The whole declarations file for an OpenAPI document."""
    schemas: dict[str, Any] = openapi.get("components", {}).get("schemas", {})
    blocks = [_declaration(name, schemas[name]) for name in sorted(schemas)]
    return HEADER + "\n" + "\n".join(blocks)
