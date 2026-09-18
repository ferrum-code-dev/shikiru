from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from jinja2 import UndefinedError

from shikiru.prompts.renderer import REPORT_INSTRUCTION, PromptName, PromptRenderer

if TYPE_CHECKING:
    from pathlib import Path

_REQUEST = "Add retries to the flaky parser"
_FENCED_REPORT = '```json\n{"status": "completed"}\n```'


def _full_context(name: PromptName) -> dict[str, Any]:
    context: dict[str, Any] = {
        "request": _REQUEST,
        "trackers": ["tasks/plan.md", "tasks/todo.md"],
        "reports": [{"label": "Implementation", "json": '{"status": "completed"}'}],
    }
    if name is PromptName.CONSULT:
        context["question"] = "Which retry library should I use?"
    if name is PromptName.VERIFY:
        context["facts"] = "git log --oneline base..HEAD"
        context["final_round"] = False
    if name in (PromptName.CONFLICTS, PromptName.HANDOFF):
        context["facts"] = "git status --porcelain"
    return context


@pytest.mark.parametrize("name", list(PromptName))
def test_every_template_includes_the_request_and_the_report_instruction(
    tmp_path: Path, name: PromptName
) -> None:
    renderer = PromptRenderer(tmp_path)

    rendered = renderer.render(name, _full_context(name))

    assert _REQUEST in rendered
    assert REPORT_INSTRUCTION in rendered


@pytest.mark.parametrize("name", list(PromptName))
def test_every_template_fences_earlier_reports(tmp_path: Path, name: PromptName) -> None:
    renderer = PromptRenderer(tmp_path)

    rendered = renderer.render(name, _full_context(name))

    assert _FENCED_REPORT in rendered


@pytest.mark.parametrize("name", list(PromptName))
def test_every_template_raises_on_a_missing_variable(tmp_path: Path, name: PromptName) -> None:
    renderer = PromptRenderer(tmp_path)
    context = _full_context(name)
    del context["request"]

    with pytest.raises(UndefinedError):
        renderer.render(name, context)


def test_a_project_override_replaces_the_packaged_template(tmp_path: Path) -> None:
    overrides_dir = tmp_path / ".shikiru" / "prompts"
    overrides_dir.mkdir(parents=True)
    (overrides_dir / "triage.md.j2").write_text("CUSTOM {{ request }}\n{{ report_instruction }}\n")
    renderer = PromptRenderer(tmp_path)

    rendered = renderer.render(
        PromptName.TRIAGE, {"request": _REQUEST, "trackers": [], "reports": []}
    )

    assert rendered.startswith("CUSTOM")
