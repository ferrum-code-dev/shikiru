"""Renders turn prompts from packaged templates, overridable per project."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import TYPE_CHECKING

from jinja2 import (
    BaseLoader,
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    StrictUndefined,
)

from shikiru.agents.contracts import shikiru_command
from shikiru.domain.fencing import fence
from shikiru.paths import PROMPT_OVERRIDES_RELATIVE_PATH

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

TEMPLATES_PACKAGE = "shikiru.prompts"
TEMPLATES_PACKAGE_PATH = "templates"
TEMPLATE_FILENAME_SUFFIX = ".md.j2"

REPORT_DRAFT_FILENAME = "report.draft.json"
REPORT_INSTRUCTION = (
    f"Write the report JSON matching `report.schema.json` to `{REPORT_DRAFT_FILENAME}` in this "
    "prompt's directory, never inside the workspace, and run "
    f"`{shikiru_command('report', '--file')} <that file>`. If it exits non-zero, fix it and run "
    "it again. Then end your turn."
)
OPERATOR_MESSAGES_KEY = "operator_messages"
ALREADY_DONE_SENTENCE = "If this work is already done in the workspace, verify it and report."


class PromptName(StrEnum):
    """One packaged template name, also its filename stem."""

    TRIAGE = auto()
    PLAN = auto()
    IMPLEMENT = auto()
    REWORK = auto()
    CONSULT = auto()
    VERIFY = auto()
    CONFLICTS = auto()
    CONTINUE = auto()
    HANDOFF = auto()


class PromptRenderer:
    """Renders a `PromptName` template, preferring a project's `.shikiru/prompts/` override."""

    def __init__(self, project_dir: Path) -> None:
        """Build the Jinja2 environment: project overrides first, packaged templates beneath."""
        loaders: list[BaseLoader] = []
        overrides_dir = project_dir / PROMPT_OVERRIDES_RELATIVE_PATH
        if overrides_dir.is_dir():
            loaders.append(FileSystemLoader(overrides_dir))
        loaders.append(PackageLoader(TEMPLATES_PACKAGE, TEMPLATES_PACKAGE_PATH))
        self._env = Environment(  # noqa: S701 -- Markdown prompts, not HTML; autoescape doesn't apply
            loader=ChoiceLoader(loaders),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._env.filters["fenced"] = fence

    def render(self, name: PromptName, context: Mapping[str, object]) -> str:
        """Render `name`'s template against `context`, raising on any undefined variable."""
        template = self._env.get_template(f"{name.value}{TEMPLATE_FILENAME_SUFFIX}")
        merged = {
            "report_instruction": REPORT_INSTRUCTION,
            "already_done_sentence": ALREADY_DONE_SENTENCE,
            **context,
        }
        return template.render(**merged)
