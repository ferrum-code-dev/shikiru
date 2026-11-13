"""A fake `muse` executable on `PATH`, configured per test through environment variables."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.plugins.agents.muse.options import API_KEY_ENV_VAR, MUSE_COMMAND

if TYPE_CHECKING:
    import pytest

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "agents" / "muse"
LOGGED_IN_VERSION = (_FIXTURES / "muse_version.txt").read_text(encoding="utf-8")
LOGGED_OUT_STDERR = (_FIXTURES / "usage_logged_out.stderr.log").read_text(encoding="utf-8")
_EXECUTABLE_MODE = 0o755
_VERSION_ENV = "FAKE_MUSE_VERSION"
_USAGE_EXIT_ENV = "FAKE_MUSE_USAGE_EXIT"
_USAGE_STDERR_ENV = "FAKE_MUSE_USAGE_STDERR"
_SERVE_MODE_ENV = "FAKE_MUSE_SERVE_MODE"
_MODEL_LIST_ENV = "FAKE_MUSE_MODEL_LIST"
_SERVE_ANSWERS = "answers"
_SERVE_SILENT = "silent"
_MODEL_LIST = [
    {"modelId": "muse-wonder-1.0", "variants": ["low", "high"], "contextLimit": 1007997},
    {"modelId": "muse-spark-1.2", "variants": ["low"], "contextLimit": 200000},
]
_FAKE_MUSE = """
import json
import os
import sys

if sys.argv[1:] == ["--version"]:
    sys.stdout.write(os.environ["FAKE_MUSE_VERSION"])
    sys.exit(0)
if sys.argv[1:] == ["usage", "--json"]:
    sys.stderr.write(os.environ["FAKE_MUSE_USAGE_STDERR"])
    sys.exit(int(os.environ["FAKE_MUSE_USAGE_EXIT"]))
if sys.argv[1:2] == ["serve"]:
    if os.environ["FAKE_MUSE_SERVE_MODE"] == "silent":
        sys.stdin.read()
        sys.exit(0)
    models = json.loads(os.environ["FAKE_MUSE_MODEL_LIST"])
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        if "id" not in request:
            continue
        result = {} if request["method"] == "initialize" else {"models": models}
        reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        sys.stdout.write(json.dumps(reply) + "\\n")
        sys.stdout.flush()
    sys.exit(0)
sys.exit(2)
"""


def install_fake_muse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    logged_in: bool = True,
    serve_answers: bool = True,
) -> None:
    """Put a fake `muse` first on `PATH` that reports a login state and a `muse serve` behaviour.

    The host's `META_API_KEY` is cleared, because a key would authenticate Muse on its own and
    hide the login state the test sets; API-key tests set it again after this call.
    """
    fake_bin = tmp_path / "fake-muse-bin"
    fake_bin.mkdir()
    executable = fake_bin / MUSE_COMMAND
    executable.write_text(f"#!{sys.executable}\n{_FAKE_MUSE}", encoding="utf-8")
    executable.chmod(_EXECUTABLE_MODE)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv(_VERSION_ENV, LOGGED_IN_VERSION)
    monkeypatch.setenv(_USAGE_EXIT_ENV, "0" if logged_in else "1")
    monkeypatch.setenv(_USAGE_STDERR_ENV, "" if logged_in else LOGGED_OUT_STDERR)
    monkeypatch.setenv(_SERVE_MODE_ENV, _SERVE_ANSWERS if serve_answers else _SERVE_SILENT)
    monkeypatch.setenv(_MODEL_LIST_ENV, json.dumps(_MODEL_LIST))


def remove_muse_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop every `PATH` entry that provides a real `muse`, leaving the other tools reachable."""
    entries = os.environ["PATH"].split(os.pathsep)
    kept = [entry for entry in entries if not (Path(entry) / MUSE_COMMAND).exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(kept))
    assert shutil.which(MUSE_COMMAND) is None
