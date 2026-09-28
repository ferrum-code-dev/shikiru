from __future__ import annotations

from operator import attrgetter
from typing import TYPE_CHECKING

import pytest

from shikiru.config.loader import load_settings
from shikiru.errors import ConfigError
from tests.support.config import MINIMAL_PROJECT_CONFIG
from tests.support.config import write_minimal_project as _project

if TYPE_CHECKING:
    from pathlib import Path


def test_project_file_overrides_packaged_defaults(tmp_path: Path) -> None:
    project_dir = _project(tmp_path, "\n[orchestration]\nmax_parallel = 4\n")

    settings = load_settings(project_dir)

    assert settings.orchestration.max_parallel == 4
    assert settings.orchestration.max_iterations == 5
    assert settings.project.trackers == []


def test_deep_merge_keeps_sibling_keys_from_lower_sources(tmp_path: Path) -> None:
    project_dir = _project(tmp_path, "\n[orchestration.resources]\nmax_cpu_pct = 50\n")

    settings = load_settings(project_dir)

    assert settings.orchestration.resources.max_cpu_pct == 50
    assert settings.orchestration.resources.min_free_ram_gb == 4


def test_deep_merge_replaces_arrays_instead_of_concatenating(tmp_path: Path) -> None:
    project_dir = _project(
        tmp_path,
        '\n[dashboard]\nwidgets = ["header"]\n',
    )

    settings = load_settings(project_dir)

    assert settings.dashboard.widgets == ["header"]


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


@pytest.mark.parametrize(
    "override",
    [
        pytest.param("orchestration.max_parallel=3,", id="trailing-comma"),
        pytest.param("project.name=demo team", id="multiple-words"),
    ],
)
def test_set_override_rejects_malformed_values(tmp_path: Path, override: str) -> None:
    project_dir = _project(tmp_path)

    with pytest.raises(ConfigError) as excinfo:
        load_settings(project_dir, overrides=[override])

    assert "--set: " in str(excinfo.value)
    assert "neither a TOML value nor a bare word" in str(excinfo.value)


def test_set_override_without_equals_sign_is_a_config_error(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)

    with pytest.raises(ConfigError, match="--set"):
        load_settings(project_dir, overrides=["orchestration.max_parallel"])


def test_invalid_value_in_project_file_names_file_and_key_path(tmp_path: Path) -> None:
    project_dir = _project(tmp_path, '\n[orchestration]\nmax_parallel = "three"\n')

    with pytest.raises(ConfigError) as excinfo:
        load_settings(project_dir)

    assert str(excinfo.value).startswith(".shikiru/config.toml: orchestration.max_parallel: ")


def test_invalid_value_from_set_override_names_set_and_key_path(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)

    with pytest.raises(ConfigError) as excinfo:
        load_settings(project_dir, overrides=["orchestration.max_parallel=[1,2]"])

    assert str(excinfo.value).startswith("--set: orchestration.max_parallel:")


def test_invalid_toml_syntax_in_project_file_names_the_file(tmp_path: Path) -> None:
    shikiru_dir = tmp_path / ".shikiru"
    shikiru_dir.mkdir()
    (shikiru_dir / "config.toml").write_text("[project\nname = 'demo'\n")

    with pytest.raises(ConfigError, match=r"^\.shikiru/config\.toml: "):
        load_settings(tmp_path)


def test_missing_project_file_reports_the_required_project_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"^defaults\.toml: project\.name: Field required"):
        load_settings(tmp_path)


def test_missing_required_key_is_attributed_to_the_project_config_file(tmp_path: Path) -> None:
    shikiru_dir = tmp_path / ".shikiru"
    shikiru_dir.mkdir()
    (shikiru_dir / "config.toml").write_text(MINIMAL_PROJECT_CONFIG.replace('name = "demo"\n', ""))

    with pytest.raises(ConfigError) as excinfo:
        load_settings(tmp_path)

    assert str(excinfo.value).startswith(".shikiru/config.toml: project.name: Field required")
