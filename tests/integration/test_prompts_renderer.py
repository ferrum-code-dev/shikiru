from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from jinja2 import UndefinedError

from shikiru.domain.fencing import fence
from shikiru.prompts.renderer import (
    REPORT_INSTRUCTION,
    REPOSITORY_INSTRUCTIONS_SENTENCE,
    PromptName,
    PromptRenderer,
)

if TYPE_CHECKING:
    from pathlib import Path

_REQUEST = "Add retries to the flaky parser"
_REPORT_JSON = '{"status": "completed"}'
_FENCED_REPORT_JSON = [
    _REPORT_JSON,
    '{"note": "closes a ``` fence early"}',
    '{"note": "closes a ```` fence early"}',
]


def _full_context(name: PromptName, *, report_json: str = _REPORT_JSON) -> dict[str, Any]:
    context: dict[str, Any] = {
        "request": _REQUEST,
        "trackers": ["tasks/plan.md", "tasks/todo.md"],
        "reports": [{"label": "Implementation", "json": report_json}],
        "required_mode": "",
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
def test_every_template_includes_the_request_and_repository_pointer_and_ends_with_the_report(
    tmp_path: Path, name: PromptName
) -> None:
    renderer = PromptRenderer(tmp_path)

    rendered = renderer.render(name, _full_context(name))

    assert _REQUEST in rendered
    assert REPOSITORY_INSTRUCTIONS_SENTENCE in rendered
    assert rendered.rstrip().endswith(REPORT_INSTRUCTION)


@pytest.mark.parametrize("report_json", _FENCED_REPORT_JSON)
@pytest.mark.parametrize("name", list(PromptName))
def test_every_template_fences_earlier_reports_even_with_backticks_inside(
    tmp_path: Path, name: PromptName, report_json: str
) -> None:
    renderer = PromptRenderer(tmp_path)

    rendered = renderer.render(name, _full_context(name, report_json=report_json))

    assert fence(report_json, "json") in rendered


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
