"""Schema preparation shared by agent plugins that constrain one-shot output."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

_TYPE_FIELD = "type"
_OBJECT_TYPE = "object"
_ADDITIONAL_PROPERTIES_FIELD = "additionalProperties"
_CLOSED_OBJECTS = False


def strict_schema(value: object) -> object:
    """Return `value` with every object schema closed to undeclared properties."""
    if isinstance(value, Mapping):
        typed_value = cast("Mapping[str, object]", value)
        schema = {key: strict_schema(item) for key, item in typed_value.items()}
        if schema.get(_TYPE_FIELD) == _OBJECT_TYPE:
            schema.setdefault(_ADDITIONAL_PROPERTIES_FIELD, _CLOSED_OBJECTS)
        return schema
    if isinstance(value, list):
        return [strict_schema(item) for item in cast("list[object]", value)]
    return value
