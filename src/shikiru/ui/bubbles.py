"""Speech bubbles for the dialogue widget (docs/design/06-operator.md §6.3)."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from shikiru.domain.tasks import Role
from shikiru.ui.glyphs import GLYPH_ENGINE, ROLE_GLYPHS
from shikiru.ui.tokens import ENGINE_COLOR, ROLE_COLORS

_SPEAKER_LABELS: dict[Role | None, str] = {
    Role.LEADER: "Leader",
    Role.IMPLEMENTER: "Implementer",
    Role.HELPER: "Helper",
    None: "Engine",
}


@dataclass(frozen=True)
class SpeechBubble:
    """Render one event as a one-line, speaker-coloured dialogue bubble."""

    role: Role | None
    text: str
    task_id: str
    age: str

    def render(self) -> Text:
        """Render the speaker, shortened display text, task and event age."""
        color = ROLE_COLORS[self.role] if self.role is not None else ENGINE_COLOR
        glyph = ROLE_GLYPHS[self.role] if self.role is not None else GLYPH_ENGINE
        speaker = _SPEAKER_LABELS[self.role]
        line = " ".join(self.text.split())
        result = Text()
        result.append(f"{glyph} {speaker}: ", style=color)
        result.append(f"“{line}”")
        result.append(f"   {self.task_id} · {self.age}", style="dim")
        return result
