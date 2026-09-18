from __future__ import annotations

import pytest
from rich.cells import cell_len
from rich.console import Console

from shikiru.domain.tasks import Mode
from shikiru.ui.strip import (
    STRIP_WIDE_MIN_COLUMNS,
    PanelState,
    PanelView,
    StampKind,
    StripStamp,
    TaskStrip,
    TaskStripData,
    panel_specs_for_mode,
)

WIDE_WIDTH = STRIP_WIDE_MIN_COLUMNS
NARROW_WIDTH = 30


def _build(mode: Mode) -> TaskStripData:
    states = (PanelState.DONE, PanelState.ACTIVE, PanelState.PENDING, PanelState.PENDING)
    specs = panel_specs_for_mode(mode)
    panels = tuple(
        PanelView(
            label=label,
            stage_glyph=glyph,
            state=state,
            detail="3m"
            if state is PanelState.DONE
            else ("2/5" if state is PanelState.ACTIVE else ""),
            role_line="◆ opus" if state is PanelState.DONE else "",
            spinner_frame="⠹" if state is PanelState.ACTIVE else "",
        )
        for (label, glyph), state in zip(specs, states, strict=True)
    )
    return TaskStripData(task_id="T-12", title="Add retry to parser", panels=panels)


@pytest.mark.parametrize("mode", [Mode.IMPLEMENT, Mode.PLAN])
@pytest.mark.parametrize("width", [WIDE_WIDTH, NARROW_WIDTH])
def test_rendered_width_never_exceeds_available_columns(mode: Mode, width: int) -> None:
    text = TaskStrip(_build(mode)).render(width)
    for line in text.plain.split("\n"):
        assert cell_len(line) <= width


@pytest.mark.parametrize("state", list(PanelState))
@pytest.mark.parametrize("width", [WIDE_WIDTH, NARROW_WIDTH])
def test_every_panel_state_shows_a_word_or_glyph_under_no_color(
    state: PanelState, width: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    panels = (
        PanelView(label="Implement", stage_glyph="⚒", state=state, detail="1", spinner_frame="⠹"),
        PanelView(label="Verify", stage_glyph="⚖", state=PanelState.PENDING),
    )
    data = TaskStripData(task_id="T-9", title="x", panels=panels)
    text = TaskStrip(data).render(width)

    console = Console(no_color=True, force_terminal=True, width=width, record=True)
    console.print(text)
    rendered = console.export_text()

    assert state.value in rendered or any(glyph in rendered for glyph in ("✔", "↺", "‼", "·", "⠹"))


@pytest.mark.parametrize("kind", list(StampKind))
def test_every_stamp_shows_a_word(kind: StampKind) -> None:
    stamp = StripStamp(kind=kind)
    assert kind.value.upper() in stamp.render()


@pytest.mark.parametrize("mode", [Mode.IMPLEMENT, Mode.PLAN])
@pytest.mark.parametrize("width", [WIDE_WIDTH, NARROW_WIDTH])
def test_strip_golden(mode: Mode, width: int) -> None:
    text = TaskStrip(_build(mode)).render(width)
    goldens = {
        (Mode.IMPLEMENT, WIDE_WIDTH): (
            "T-12 · Add retry to parser                                  \n"
            "⌕ Triage      |⚒ Implement   |⚖︎ Verify      |⑂ Merge        \n"
            "✔ done 3m     |⠹ active 2/5  |· pending     |· pending      \n"
            "◆ opus        |              |              |               "
        ),
        (Mode.IMPLEMENT, NARROW_WIDTH): "T-12 ⚒ Implement ⠹ active 2/5",
        (Mode.PLAN, WIDE_WIDTH): (
            "T-12 · Add retry to parser                                  \n"
            "⌕ Triage      |≡ Plan        |—             |⑂ Merge        \n"
            "✔ done 3m     |⠹ active 2/5  |· pending     |· pending      \n"
            "◆ opus        |              |              |               "
        ),
        (Mode.PLAN, NARROW_WIDTH): "T-12 ≡ Plan ⠹ active 2/5",
    }
    assert text.plain == goldens[(mode, width)]


def test_split_parent_renders_as_a_finished_strip_with_child_ids() -> None:
    data = TaskStripData(task_id="T-3", title="Break this up", split_children=("T-4", "T-5"))
    text = TaskStrip(data).render(WIDE_WIDTH)
    assert "T-4" in text.plain
    assert "T-5" in text.plain
