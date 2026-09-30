"""The import-linter denylists must name every package, so adding one forces a decision (02 §2)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE = _ROOT / "src" / "shikiru"
_PLUGIN_SOURCE = "shikiru.plugins.agents"
_ENGINE_SOURCE = "shikiru.engine"
_AGENTS = "shikiru.agents"
_PLUGIN_ALLOWED = frozenset({"agents", "clock", "errors", "process", "plugins"})
_ENGINE_ALLOWED_AGENTS = frozenset({"contracts"})
_PLUGIN_ALLOWED_AGENTS = frozenset({"contracts", "sdk"})


def _forbidden(source: str) -> set[str]:
    contracts = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "importlinter"
    ]["contracts"]
    (contract,) = (c for c in contracts if c.get("source_modules") == [source])
    return set(contract["forbidden_modules"])


def _children(package: Path) -> set[str]:
    return {
        entry.stem
        for entry in package.iterdir()
        if entry.stem != "__pycache__" and (entry.suffix == ".py" or entry.is_dir())
    } - {"__init__"}


@pytest.mark.parametrize(
    ("source", "package", "prefix", "allowed"),
    [
        (_PLUGIN_SOURCE, _PACKAGE, "shikiru", _PLUGIN_ALLOWED),
        (_PLUGIN_SOURCE, _PACKAGE / "agents", _AGENTS, _PLUGIN_ALLOWED_AGENTS),
        (_ENGINE_SOURCE, _PACKAGE / "agents", _AGENTS, _ENGINE_ALLOWED_AGENTS),
    ],
)
def test_every_module_is_forbidden_or_explicitly_allowed(
    source: str, package: Path, prefix: str, allowed: frozenset[str]
) -> None:
    named = {
        forbidden.removeprefix(f"{prefix}.").split(".")[0]
        for forbidden in _forbidden(source)
        if forbidden.startswith(f"{prefix}.")
    }
    undecided = _children(package) - named - allowed
    assert not undecided, f"decide {sorted(undecided)} for {source}: forbid it or allow it here"
