from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from shikiru.domain.fencing import fence
from shikiru.prompts.renderer import (
    REPORT_INSTRUCTION,
    PromptName,
    PromptRenderer,
)

if TYPE_CHECKING:
    from pathlib import Path

_REQUEST = "Add retries to the flaky parser"
_WORKSPACE = "/projects/demo/.shikiru/worktrees/T-7"
_BASE = "release-7"
_FENCING_REPORT_JSON = '{"note": "closes a ```` fence early"}'


def _full_context(name: PromptName) -> dict[str, Any]:
    context: dict[str, Any] = {
        "request": _REQUEST,
        "workspace": _WORKSPACE,
        "trackers": ["tasks/plan.md", "tasks/todo.md"],
        "reports": [{"label": "Implementation", "json": _FENCING_REPORT_JSON}],
        "required_mode": "",
        "operator_messages": [],
    }
    if name is PromptName.CONSULT:
        context["question"] = "Which retry library should I use?"
    if name is PromptName.VERIFY:
        context["facts"] = "git log --oneline base..HEAD"
        context["base"] = _BASE
        context["final_round"] = False
    if name is PromptName.CONFLICTS:
        context["conflict"] = ""
    if name in (PromptName.CONFLICTS, PromptName.HANDOFF):
        context["facts"] = "git status --porcelain"
    return context


@pytest.mark.parametrize("name", list(PromptName))
def test_every_template_renders_the_request_the_workspace_fenced_reports_and_ends_with_the_report(
    tmp_path: Path, name: PromptName
) -> None:
    renderer = PromptRenderer(tmp_path)

    rendered = renderer.render(name, _full_context(name))

    assert _REQUEST in rendered
    assert f"`{_WORKSPACE}`" in rendered
    assert fence(_FENCING_REPORT_JSON, "json") in rendered
    assert rendered.rstrip().endswith(REPORT_INSTRUCTION)


def test_verify_points_the_leader_at_the_diff_against_the_base(tmp_path: Path) -> None:
    rendered = PromptRenderer(tmp_path).render(PromptName.VERIFY, _full_context(PromptName.VERIFY))

    assert f"git diff {_BASE}...HEAD" in rendered


def test_a_project_override_replaces_the_packaged_template(tmp_path: Path) -> None:
    overrides_dir = tmp_path / ".shikiru" / "prompts"
    overrides_dir.mkdir(parents=True)
    (overrides_dir / "triage.md.j2").write_text("CUSTOM {{ request }}\n{{ report_instruction }}\n")
    renderer = PromptRenderer(tmp_path)

    rendered = renderer.render(
        PromptName.TRIAGE, {"request": _REQUEST, "trackers": [], "reports": []}
    )

    assert rendered.startswith("CUSTOM")
