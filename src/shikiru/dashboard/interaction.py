from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Input, Select, TextArea

if TYPE_CHECKING:
    from textual.widget import Widget


def native_editor_has_focus(focused: Widget | None) -> bool:
    current = focused
    while current is not None:
        if isinstance(current, (Input, Select, TextArea)):
            return True
        current = current.parent
    return False
