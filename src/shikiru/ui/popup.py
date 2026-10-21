from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical
from textual.css.scalar import Unit
from textual.geometry import Size

from shikiru.ui.keys import key_hint
from shikiru.ui.screen import widget_is_attached

if TYPE_CHECKING:
    from rich.text import Text
    from textual import events
    from textual.widget import Widget

POPUP_FRAME_CLASS = "popup-frame"
POPUP_HEADER_CLASS = "popup-header"
POPUP_TITLE_CLASS = "popup-title"
POPUP_CLOSE_CLASS = "popup-close-control"
POPUP_RULE_CLASS = "popup-rule"
POPUP_BODY_CLASS = "popup-body"
POPUP_SECONDARY_CLASS = "popup-secondary"
POPUP_EDIT_CLASS = "popup-edit"
POPUP_CLOSE_LABEL = "Esc \N{MULTIPLICATION SIGN}"


def popup_close_label() -> Text:
    """Return the Esc close label with its key in the key style."""
    return key_hint(POPUP_CLOSE_LABEL)


class PopupFrame(Vertical):
    """Keep a content-sized popup frame stable when its screen is resized."""

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        widget_id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(
            *children,
            name=name,
            id=widget_id,
            classes=classes,
            disabled=disabled,
            markup=markup,
        )
        self._content_ready = False
        self._preferred_size: Size | None = None
        self._applied_size: Size | None = None

    def content_ready(self) -> None:
        """Mark meaningful initial content as ready for preferred-size capture."""
        if not self.is_mounted or not widget_is_attached(self):
            return
        self._content_ready = True
        self.call_after_refresh(self.update_preferred_size)

    def reset_preferred_size(self) -> None:
        """Forget the prior measurement when a popup changes content purpose."""
        self._preferred_size = None
        self._applied_size = None
        self._content_ready = False
        self.remove_class("stable")
        self.styles.width = "auto"
        self.styles.height = "auto"

    def on_resize(self, event: events.Resize) -> None:
        del event
        self.call_after_refresh(self.update_preferred_size)

    def update_preferred_size(self) -> None:
        """Capture an unclamped outer size once, then clamp it to the current screen."""
        if not self.is_mounted or not widget_is_attached(self):
            return
        viewport = self.screen.size
        if self._preferred_size is None:
            self._remember_preferred_size()
        if self._preferred_size is not None:
            self._apply_preferred_size(viewport)

    def _remember_preferred_size(self) -> None:
        if not self._content_ready:
            return
        height = self.styles.height
        children = tuple(self.children)
        viewport = self.screen.size
        natural_width = (
            max(
                (child.virtual_size.width + child.styles.margin.width for child in children),
                default=0,
            )
            + self.styles.gutter.width
        )
        max_width = self.styles.max_width
        max_width_cells = max_width.cells if max_width is not None else None
        preferred_width = max(
            self.outer_size.width,
            max_width_cells if max_width_cells is not None else natural_width,
        )
        relative_height = height is not None and height.unit in {
            Unit.PERCENT,
            Unit.HEIGHT,
            Unit.VIEW_HEIGHT,
        }
        height_clamped = self.outer_size.height >= viewport.height - self.styles.margin.height
        if relative_height and not height_clamped:
            natural_height = self.outer_size.height
        elif relative_height:
            natural_height = sum(_preferred_child_height(child) for child in children)
            natural_height += self.styles.gutter.height
        else:
            content_width = max(0, preferred_width - self.styles.gutter.width)
            natural_height = sum(
                _preferred_child_height_at_width(child, viewport, content_width)
                for child in children
            )
            natural_height += self.styles.gutter.height
        preferred_height = (
            natural_height
            if relative_height and height_clamped
            else max(self.outer_size.height, natural_height)
        )
        self._preferred_size = Size(
            preferred_width,
            preferred_height,
        )
        self.add_class("stable")

    def _apply_preferred_size(self, viewport: Size) -> None:
        if self._preferred_size is None:
            return
        margin = self.styles.margin
        minimum_height = sum(_minimum_child_height(child) for child in self.children)
        minimum_height += self.styles.gutter.height
        size = Size(
            max(1, min(self._preferred_size.width, viewport.width - margin.width)),
            max(
                1,
                min(
                    max(self._preferred_size.height, minimum_height),
                    viewport.height - margin.height,
                ),
            ),
        )
        if size != self._applied_size:
            self.styles.width = size.width
            self.styles.height = size.height
            self._applied_size = size


def _preferred_child_height(child: Widget) -> int:
    if not child.display:
        height = child.styles.height
        fixed_height = height.cells if height is not None else None
        return (fixed_height or 0) + child.styles.margin.height
    height = child.styles.height
    fixed_height = height.cells if height is not None else None
    return (
        max(child.outer_size.height, child.virtual_size.height, fixed_height or 0)
        + child.styles.margin.height
    )


def _preferred_child_height_at_width(child: Widget, viewport: Size, width: int) -> int:
    if not child.display:
        return _preferred_child_height(child)
    content_width = max(0, width - child.styles.gutter.width)
    content_height = child.get_content_height(Size(content_width, 0), viewport, content_width)
    height = child.styles.height
    fixed_height = height.cells if height is not None else None
    return (
        max(
            child.outer_size.height,
            fixed_height or 0,
            content_height + child.styles.gutter.height,
        )
        + child.styles.margin.height
    )


def _minimum_child_height(child: Widget) -> int:
    if not child.display:
        return 0
    height = child.styles.height
    fixed_height = height.cells if height is not None else None
    minimum_height = child.styles.min_height
    minimum_height_cells = minimum_height.cells if minimum_height is not None else None
    flexible_height = height is not None and height.unit is Unit.FRACTION
    outer_height = 0 if flexible_height else child.outer_size.height
    return (
        max(outer_height, fixed_height or 0, minimum_height_cells or 0) + child.styles.margin.height
    )


POPUP_BUTTON_CSS = """
.popup-frame Button.-style-default {
    color: $primary-text; background: $surface; border: solid $neutral-border;
    border-top: solid $neutral-border; border-bottom: solid $neutral-border;
    text-style: none;
}
.popup-frame Button.-style-default.-primary {
    border: solid $neutral-border;
    border-top: solid $neutral-border; border-bottom: solid $neutral-border;
}
.popup-frame Button.-style-default:focus,
.popup-frame Button.-style-default.-primary:focus {
    color: $primary-text; background: $selected-surface;
    border: solid $focus; border-top: solid $focus; border-bottom: solid $focus;
    text-style: reverse;
}
.popup-frame Button.-style-default:disabled,
.popup-frame Button.-style-default.-primary:disabled {
    color: $secondary-text; background: $surface;
    border: solid $neutral-border;
    border-top: solid $neutral-border; border-bottom: solid $neutral-border;
    text-opacity: 76%;
}
"""

POPUP_CONTROL_CSS = """
.popup-frame Button:focus {
    color: $primary-text; background: $selected-surface; text-style: reverse;
}
.popup-frame Button:disabled { color: $secondary-text; }
.popup-frame SelectCurrent {
    border: solid $neutral-border; padding: 0 1; background: $detail-surface;
}
.popup-frame Select:focus > SelectCurrent,
.popup-frame Select.-expanded > SelectCurrent {
    border: solid $focus; background: $selected-surface;
}
.popup-frame SelectOverlay {
    border: solid $neutral-border; scrollbar-size-vertical: $scrollbar-width;
}
.popup-frame SelectOverlay:focus { border: solid $focus; }
.popup-frame TextArea { scrollbar-size-vertical: $scrollbar-width; }
.popup-frame TextArea.popup-edit {
    height: auto; min-height: 2; max-height: 6; padding: 0;
    border: none; border-bottom: solid $neutral-border; background: $surface;
}
.popup-frame TextArea.popup-edit > .text-area--cursor-line { background: transparent; }
.popup-frame TextArea.popup-edit:focus {
    border: none; border-bottom: solid $focus; background: $selected-surface;
}
.popup-frame Select:disabled > SelectCurrent { color: $secondary-text; }
.popup-frame Button.popup-close-control {
    width: auto; min-width: 5; height: 1; margin: 0; padding: 0 1;
    border: none; border-top: none; border-bottom: none;
    background: $surface; color: $primary-text; text-style: none;
}
.popup-frame Button.popup-close-control.-style-default:focus {
    border: none; border-top: none; border-right: none; border-bottom: none; border-left: none;
    background: $selected-surface; color: $primary-text; text-style: reverse;
}
"""

POPUP_CSS = (
    """
.popup-frame {
    width: 100%; max-width: 64; height: auto; max-height: 100%; margin: 1 2;
    padding: 1 2; border: solid $steel-blue; background: $surface; color: $primary-text;
}
.popup-header { width: 100%; height: 1; }
.popup-title { width: 1fr; height: 1; color: $primary-text; text-style: bold; }
.popup-frame Rule.popup-rule { width: 100%; height: 1; margin: 0; color: $neutral-border; }
.popup-body {
    height: auto; max-height: 1fr; min-height: 0; padding: 0;
}
.popup-frame.stable > .popup-body { height: 1fr; }
.popup-body, .popup-frame #task-detail-body {
    overflow-x: hidden; overflow-y: auto;
    scrollbar-size-horizontal: 0; scrollbar-size-vertical: $scrollbar-width;
}
.popup-secondary { color: $secondary-text; }
"""
    + POPUP_BUTTON_CSS
    + POPUP_CONTROL_CSS
)


def prepare_popup(screen: Widget) -> None:
    screen.styles.align_horizontal = "center"
    screen.styles.align_vertical = "middle"
    screen.styles.background = "transparent"
    screen.styles.padding = 0
