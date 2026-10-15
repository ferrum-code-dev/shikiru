"""The one-line text filter of the Agent activity list: `role: any · turn: any`."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

from shikiru.dashboard.section_rows import ROLE_LABELS, turn_label
from shikiru.domain.tasks import Role
from shikiru.ui.section import BOLD_STEEL, GROUP_SEPARATOR, grey
from shikiru.ui.tokens import PRIMARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.binding import BindingType

ANY_LABEL = "any"
ROLE_CHOICES = (Role.LEADER.value, Role.IMPLEMENTER.value, Role.HELPER.value)
ROLE_NAME = "role:"
TURN_NAME = "turn:"
CYCLE_ROLE_KEY = "r"
CYCLE_TURN_KEY = "t"
CLEAR_KEY = "x"
CLOSE_KEY = "f"


def next_choice(current: str | None, choices: Sequence[str]) -> str | None:
    """Return the choice after `current`; `None` (any) follows the last, or starts the cycle."""
    cycle = [*choices, None]
    return cycle[choices.index(current) + 1] if current in choices else cycle[0]


class TimelineFilterBar(Static, can_focus=True):
    """Role and turn filter shown as text; `r` and `t` cycle them, `x` clears, `f` closes."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(CYCLE_ROLE_KEY, "cycle_role", "Role", show=False),
        Binding(CYCLE_TURN_KEY, "cycle_turn", "Turn", show=False),
        Binding(CLEAR_KEY, "clear", "Clear", show=False),
        Binding(CLOSE_KEY, "close", "Close", show=False),
    ]
    DEFAULT_CSS = """
    TimelineFilterBar { height: 1; width: 100%; margin: 1 0 0 0; }
    TimelineFilterBar:focus { background-tint: transparent; }
    """

    class Changed(Message):
        """The role or turn filter changed."""

        def __init__(self, role: str | None, turn: str | None) -> None:
            super().__init__()
            self.role = role
            self.turn = turn

    class Closed(Message):
        """The operator closed the bar."""

    def __init__(self, *, widget_id: str) -> None:
        """Create a hidden bar with no filter."""
        super().__init__("", id=widget_id, markup=False)
        self.display = False
        self._role: str | None = None
        self._turn: str | None = None
        self._turns: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        """Whether a role or turn filter is applied."""
        return self._role is not None or self._turn is not None

    def on_mount(self) -> None:
        self._paint()

    def on_focus(self) -> None:
        self._paint()

    def on_blur(self) -> None:
        self._paint()

    def set_turns(self, turns: Sequence[str]) -> None:
        """Set the turns `t` cycles through."""
        self._turns = tuple(turns)

    def reset(self) -> None:
        """Forget the filter and the known turns."""
        self._role = self._turn = None
        self._turns = ()
        self._paint()

    def action_cycle_role(self) -> None:
        """Pick the next role, or any."""
        self._set(next_choice(self._role, ROLE_CHOICES), self._turn)

    def action_cycle_turn(self) -> None:
        """Pick the next seen turn, or any."""
        self._set(self._role, next_choice(self._turn, self._turns))

    def action_clear(self) -> None:
        """Drop both filters."""
        self._set(None, None)

    def action_close(self) -> None:
        """Ask the owner to hide the bar."""
        self.post_message(self.Closed())

    def _set(self, role: str | None, turn: str | None) -> None:
        if (role, turn) == (self._role, self._turn):
            return
        self._role, self._turn = role, turn
        self._paint()
        self.post_message(self.Changed(role, turn))

    def _paint(self) -> None:
        name_style = BOLD_STEEL if self.has_focus else ""
        role = ROLE_LABELS[self._role] if self._role is not None else ANY_LABEL
        line = Text()
        for index, (name, value) in enumerate(
            ((ROLE_NAME, role), (TURN_NAME, turn_label(self._turn) or ANY_LABEL))
        ):
            if index:
                line.append_text(grey(GROUP_SEPARATOR))
            line.append_text(Text(name, style=name_style) if name_style else grey(name))
            line.append(f" {value}", style=PRIMARY_TEXT_COLOR)
        self.update(line)
