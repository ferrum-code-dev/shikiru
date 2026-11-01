"""Render keys and values as TOML text that reads back unchanged."""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, cast

from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
SHORT_ESCAPES: Mapping[str, str] = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}
LAST_CONTROL_CODE = 0x1F
DELETE_CODE = 0x7F
VALUE_SEPARATOR = ", "


def toml_string(text: str) -> str:
    """Return `text` as a TOML basic string, keeping non-ASCII characters literal."""
    parts: list[str] = []
    for character in text:
        code = ord(character)
        if character in SHORT_ESCAPES:
            parts.append(SHORT_ESCAPES[character])
        elif code <= LAST_CONTROL_CODE or code == DELETE_CODE:
            parts.append(f"\\u{code:04X}")
        else:
            parts.append(character)
    return f'"{"".join(parts)}"'


def toml_key(key: str) -> str:
    """Return `key` bare when TOML allows it, otherwise as one quoted key."""
    return key if BARE_KEY.fullmatch(key) else toml_string(key)


def toml_value(value: object) -> str:
    """Return a bool, int, finite float, string or list of those as TOML text."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, (list, tuple)):
        items = cast("Sequence[object]", value)
        return f"[{VALUE_SEPARATOR.join(toml_value(item) for item in items)}]"
    message = f"cannot write {value!r} to the project config"
    raise ConfigError(message)
