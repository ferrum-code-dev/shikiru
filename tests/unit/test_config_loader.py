from __future__ import annotations

from operator import attrgetter
from typing import TYPE_CHECKING

import pytest

from shikiru.config.loader import load_settings
from shikiru.config.settings import PaneLeaf
from shikiru.errors import ConfigError
from tests.support.config import MINIMAL_PROJECT_CONFIG
from tests.support.config import write_minimal_project as _project

if TYPE_CHECKING:
    from pathlib import Path


def test_deep_merge_keeps_sibling_keys_from_lower_sources(tmp_path: Path) -> None:
    project_dir = _project(tmp_path, "\n[orchestration.resources]\nmax_cpu_pct = 50\n")

    settings = load_settings(project_dir)

    assert settings.orchestration.resources.max_cpu_pct == 50
    assert settings.orchestration.resources.min_free_ram_gb == 4


def test_unavailable_retry_defaults_keep_the_existing_retry_schedule(tmp_path: Path) -> None:
    settings = load_settings(_project(tmp_path))
    retries = settings.orchestration.retries

    assert retries.unavailable_initial_seconds == 30
    assert retries.unavailable_multiplier == 4
    assert retries.unavailable_max_seconds == 300
    assert retries.unavailable_max_retries == 3


def test_unavailable_retry_settings_load_from_project_config(tmp_path: Path) -> None:
    project_dir = _project(
        tmp_path,
        "\n[orchestration.retries]\n"
        "unavailable_initial_seconds = 2\n"
        "unavailable_multiplier = 3\n"
        "unavailable_max_seconds = 10\n"
        "unavailable_max_retries = 4\n",
    )

    retries = load_settings(project_dir).orchestration.retries

    assert retries.unavailable_initial_seconds == 2
    assert retries.unavailable_multiplier == 3
    assert retries.unavailable_max_seconds == 10
    assert retries.unavailable_max_retries == 4


def test_deep_merge_replaces_arrays_instead_of_concatenating(tmp_path: Path) -> None:
    project_dir = _project(
        tmp_path,
        '\n[dashboard]\nwidgets = ["header"]\n',
    )

    settings = load_settings(project_dir)

    assert settings.dashboard.widgets == ["header"]


def test_explicit_task_layout_override_remains_intact(tmp_path: Path) -> None:
    project_dir = _project(
        tmp_path,
        '\n[dashboard.layout]\nsplit = "h"\npanes = [ '
        '{ pane = "system", size = 30 }, '
        '{ pane = "leader" }, '
        '{ pane = "implementer" }, '
        '{ pane = "shell", size = 20 } ]\n',
    )

    layout = load_settings(project_dir).dashboard.layout

    assert layout.split == "h"
    assert [(pane.pane, pane.size) for pane in layout.panes if isinstance(pane, PaneLeaf)] == [
        ("system", 30),
        ("leader", None),
        ("implementer", None),
        ("shell", 20),
    ]


def test_set_override_wins_over_project_file(tmp_path: Path) -> None:
    project_dir = _project(tmp_path, "\n[orchestration]\nmax_parallel = 4\n")

    settings = load_settings(project_dir, overrides=["orchestration.max_parallel=7"])

    assert settings.orchestration.max_parallel == 7


@pytest.mark.parametrize(
    ("override", "path", "expected"),
    [
        pytest.param("project.name=demo", "project.name", "demo", id="bare-word-string"),
        pytest.param("project.name=demo-project", "project.name", "demo-project", id="hyphen"),
        pytest.param("orchestration.max_parallel=3", "orchestration.max_parallel", 3, id="integer"),
        pytest.param('project.trackers=["a.md"]', "project.trackers", ["a.md"], id="list"),
    ],
)
def test_set_override_parses_toml_values_and_bare_words(
    tmp_path: Path, override: str, path: str, expected: object
) -> None:
    project_dir = _project(tmp_path)
    settings = load_settings(project_dir, overrides=[override])

    assert attrgetter(path)(settings) == expected


_PROJECT_NAME_LINE = 'name = "demo"\n'


@pytest.mark.parametrize(
    ("config", "overrides", "source"),
    [
        pytest.param(
            MINIMAL_PROJECT_CONFIG + '\n[orchestration]\nmax_parallel = "three"\n',
            [],
            ".shikiru/config.toml: orchestration.max_parallel: ",
            id="invalid-value-in-project-file",
        ),
        pytest.param(
            MINIMAL_PROJECT_CONFIG,
            ["orchestration.max_parallel=[1,2]"],
            "--set: orchestration.max_parallel:",
            id="invalid-value-from-set",
        ),
        pytest.param(
            MINIMAL_PROJECT_CONFIG,
            ["orchestration.max_parallel=3,"],
            "--set: ",
            id="malformed-set-trailing-comma",
        ),
        pytest.param(
            MINIMAL_PROJECT_CONFIG,
            ["project.name=demo team"],
            "--set: ",
            id="malformed-set-multiple-words",
        ),
        pytest.param(
            MINIMAL_PROJECT_CONFIG,
            ["orchestration.max_parallel"],
            "--set: ",
            id="set-without-equals-sign",
        ),
        pytest.param("[project\nname = 'demo'\n", [], ".shikiru/config.toml: ", id="invalid-toml"),
        pytest.param(
            MINIMAL_PROJECT_CONFIG.replace(_PROJECT_NAME_LINE, ""),
            [],
            ".shikiru/config.toml: project.name: ",
            id="missing-key-in-project-file",
        ),
        pytest.param(None, [], "defaults.toml: project.name: ", id="no-project-file"),
    ],
)
def test_a_config_error_names_the_source_that_is_wrong(
    tmp_path: Path, config: str | None, overrides: list[str], source: str
) -> None:
    if config is not None:
        shikiru_dir = tmp_path / ".shikiru"
        shikiru_dir.mkdir()
        (shikiru_dir / "config.toml").write_text(config)

    with pytest.raises(ConfigError) as excinfo:
        load_settings(tmp_path, overrides=overrides)

    assert str(excinfo.value).startswith(source)
