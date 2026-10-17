"""The Home `Tasks` label with its filter and search key hints."""

from __future__ import annotations

from rich.cells import cell_len, set_cell_size
from rich.text import Text

from shikiru.ui.section import BOLD_STEEL, grey
from shikiru.ui.tokens import PRIMARY_TEXT_COLOR

TASKS_LABEL = "Tasks"
FILTER_KEY = "f"
SEARCH_KEY = "/"
HINT_GAP = "  "
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
MIN_SEARCH_CELLS = 3
LEVEL_FULL = 0
LEVEL_NAME = 1
LEVEL_GLYPH = 2
LEVEL_KEYS = 3


def task_list_header(
    *,
    name: str,
    marker: tuple[str, str] | None,
    search: str,
    width: int,
    keys_only: bool,
) -> Text:
    """Return `Tasks` and the key hints, shortened until they fit `width` cells.

    A `width` of 0 means the width is not known yet and shows the full hint.
    """
    for level in range(LEVEL_KEYS if keys_only else LEVEL_FULL, LEVEL_KEYS):
        header = _header(level, name, marker, search)
        if width <= 0 or cell_len(header.plain) <= width:
            return header
        clipped = _clip(search, cell_len(header.plain) - width)
        if clipped is not None:
            return _header(level, name, marker, clipped)
    return _header(LEVEL_KEYS, name, marker, search)


def _clip(search: str, excess: int) -> str | None:
    keep = cell_len(search) - excess
    if not search or keep < MIN_SEARCH_CELLS:
        return None
    return set_cell_size(search, keep - 1) + ELLIPSIS


def _header(level: int, name: str, marker: tuple[str, str] | None, search: str) -> Text:
    header = Text(TASKS_LABEL)
    header.append(HINT_GAP)
    header.append_text(_filter_part(level, name, marker))
    header.append(HINT_GAP)
    header.append_text(_search_part(level, search))
    return header


def _filter_part(level: int, name: str, marker: tuple[str, str] | None) -> Text:
    part = Text(FILTER_KEY, style=BOLD_STEEL)
    if level == LEVEL_KEYS:
        return part
    if level == LEVEL_FULL:
        part.append_text(grey(" filter:"))
    part.append(" ")
    if marker is None:
        part.append(name, style=PRIMARY_TEXT_COLOR)
    elif level == LEVEL_GLYPH:
        part.append(marker[0], style=marker[1])
    else:
        part.append(marker[0], style=marker[1])
        part.append(f" {name}", style=PRIMARY_TEXT_COLOR)
    return part


def _search_part(level: int, search: str) -> Text:
    part = Text(SEARCH_KEY, style=BOLD_STEEL)
    if level == LEVEL_FULL:
        part.append_text(grey(" search"))
    if search and level <= LEVEL_NAME:
        if level == LEVEL_FULL:
            part.append_text(grey(":"))
        part.append(f" {search}", style=PRIMARY_TEXT_COLOR)
    return part
