from __future__ import annotations

import pytest
from rich.cells import cell_len

from shikiru.domain.tasks import Mode, Role, Status, Step
from shikiru.ui.strip import (
    STRIP_WIDE_MIN_COLUMNS,
    PanelState,
    PanelView,
    StampKind,
    StripStamp,
    TaskStrip,
    TaskStripData,
    panel_specs_for_mode,
    task_strip_data,
)
from shikiru.ui.tokens import (
    ALERT_COLOR,
    IDLE_COLOR,
    REWORK_COLOR,
    ROLE_COLORS,
    STYLE_BOLD,
    STYLE_DIM,
    STYLE_REVERSE,
    SUCCESS_COLOR,
)
from tests.support.factories import make_settings, make_task

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
            role_line="◆ opus"
            if state is PanelState.DONE
            else ("▲ codex/luna" if state is PanelState.ACTIVE else ""),
            spinner_frame="⠹" if state is PanelState.ACTIVE else "",
            role=Role.IMPLEMENTER if state is PanelState.ACTIVE else None,
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


def test_wide_panel_headers_are_shown_in_full_when_the_width_allows() -> None:
    text = TaskStrip(_build(Mode.IMPLEMENT)).render(WIDE_WIDTH)

    header_line = text.plain.split("\n")[1]

    assert "Implement" in header_line


def test_wide_panel_headers_are_truncated_only_when_they_do_not_fit() -> None:
    panels = tuple(
        PanelView(label=f"Very Long Label {index}", stage_glyph="⚒", state=PanelState.PENDING)
        for index in range(4)
    )
    data = TaskStripData(task_id="T-1", title="x", panels=panels)

    text = TaskStrip(data).render(WIDE_WIDTH)

    header_line = text.plain.split("\n")[1]
    assert "Very Long Label 0" not in header_line
    for line in text.plain.split("\n"):
        assert cell_len(line) <= WIDE_WIDTH


_MONOCHROME_ATTRIBUTES = (STYLE_DIM, STYLE_BOLD, STYLE_REVERSE)


@pytest.mark.parametrize(
    ("state", "role", "color"),
    [
        (PanelState.PENDING, None, IDLE_COLOR),
        (PanelState.DONE, None, SUCCESS_COLOR),
        (PanelState.REWORK, None, REWORK_COLOR),
        (PanelState.ESCALATED, None, ALERT_COLOR),
        (PanelState.ACTIVE, Role.LEADER, ROLE_COLORS[Role.LEADER]),
        (PanelState.ACTIVE, Role.IMPLEMENTER, ROLE_COLORS[Role.IMPLEMENTER]),
    ],
)
def test_panel_style_carries_the_state_or_acting_roles_colour_and_a_monochrome_attribute(
    state: PanelState, role: Role | None, color: str
) -> None:
    panel = PanelView(label="Implement", stage_glyph="⚒", state=state, role=role, detail="1")
    data = TaskStripData(task_id="T-9", title="x", panels=(panel,))

    text = TaskStrip(data).render(WIDE_WIDTH)

    body_styles = [
        span.style for span in text.spans if isinstance(span.style, str) and color in span.style
    ]
    assert body_styles
    assert any(
        any(attribute in style for attribute in _MONOCHROME_ATTRIBUTES) for style in body_styles
    )


def test_stamp_is_drawn_over_the_active_panel_body_not_the_header() -> None:
    data = _build(Mode.IMPLEMENT)
    stamped = TaskStripData(
        task_id=data.task_id,
        title=data.title,
        panels=data.panels,
        stamp=StripStamp(StampKind.PAUSED),
    )

    text = TaskStrip(stamped).render(WIDE_WIDTH)

    header_line = text.plain.split("\n")[0]
    assert "PAUSED" not in header_line
    assert "PAUSED" in text.plain


@pytest.mark.parametrize("mode", [Mode.IMPLEMENT, Mode.PLAN])
@pytest.mark.parametrize("width", [WIDE_WIDTH, NARROW_WIDTH])
def test_strip_golden(mode: Mode, width: int) -> None:
    text = TaskStrip(_build(mode)).render(width)
    goldens = {
        (Mode.IMPLEMENT, WIDE_WIDTH): (
            "┏━ T-12 · Add retry to parser ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            "┃起 ⌕ Triage┃承 ⚒ Implement┃転 ⚖︎ Verify┃結 ⑂ Merge         ┃\n"
            "┃✔ 3m       ┃⠹ ▲ 2/5       ┃·          ┃·                  ┃\n"
            "┃◆ opus     ┃▲ codex/luna  ┃           ┃                   ┃\n"
            "┗━━━━━━━━━━━┻━━━━━━━━━━━━━━┻━━━━━━━━━━━┻━━━━━━━━━━━━━━━━━━━┛"
        ),
        (Mode.IMPLEMENT, NARROW_WIDTH): "T-12 ⚒ Implement 2/5 ▲ codex/l",
        (Mode.PLAN, WIDE_WIDTH): (
            "┏━ T-12 · Add retry to parser ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            "┃起 ⌕ Triage┃承 ≡ Plan┃転 —┃結 ⑂ Merge                     ┃\n"
            "┃✔ 3m       ┃⠹ ▲ 2/5  ┃·   ┃·                              ┃\n"
            "┃◆ opus     ┃▲ codex/l┃    ┃                               ┃\n"
            "┗━━━━━━━━━━━┻━━━━━━━━━┻━━━━┻━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
        ),
        (Mode.PLAN, NARROW_WIDTH): "T-12 ≡ Plan 2/5 ▲ codex/luna ⠹",
    }
    assert text.plain == goldens[(mode, width)]


def test_split_parent_renders_as_a_finished_strip_with_child_ids() -> None:
    data = TaskStripData(task_id="T-3", title="Break this up", split_children=("T-4", "T-5"))
    text = TaskStrip(data).render(WIDE_WIDTH)
    assert "T-4" in text.plain
    assert "T-5" in text.plain


def test_submitted_split_task_renders_its_triage_stage_after_dispatch() -> None:
    task = make_task(mode=Mode.SPLIT, status=Status.RUNNING, step=Step.TRIAGE)

    text = TaskStrip(task_strip_data(task, make_settings())).render(WIDE_WIDTH)

    assert "Triage" in text.plain
