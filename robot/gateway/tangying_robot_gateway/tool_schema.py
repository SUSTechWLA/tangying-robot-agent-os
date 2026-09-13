"""Validation for the JSON Schema subset used by the robot tool catalogue."""

import math
from collections.abc import Mapping


def validate_value(value, schema, path="arguments"):
    kind = schema.get("type")
    valid = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, (list, tuple)),
        "string": isinstance(value, str),
        "number": type(value) in (int, float) and math.isfinite(value),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if kind and not valid.get(kind, False):
        raise ValueError(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if kind == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", ())) - value.keys()
        if missing:
            raise ValueError(f"{path} missing required fields: {sorted(missing)}")
        for key, item in value.items():
            if key not in properties:
                if not schema.get("additionalProperties", False):
                    raise ValueError(f"{path} contains unknown field {key!r}")
            else:
                validate_value(item, properties[key], f"{path}.{key}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf):
            raise ValueError(f"{path} has invalid length")
        for index, item in enumerate(value):
            validate_value(item, schema.get("items", {}), f"{path}[{index}]")
    elif kind in ("number", "integer"):
        if not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            raise ValueError(f"{path} is outside allowed range")
