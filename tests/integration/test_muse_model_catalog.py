"""Muse model catalog read through the real process runner and a fake `muse serve` on `PATH`."""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import ModelChoice
from shikiru.plugins.agents.muse.catalog import read_model_catalog
from shikiru.plugins.agents.muse.options import MUSE_COMMAND
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

_ORDERED = "ordered"
_REJECTED = "rejected"
_SILENT = "silent"
_INVALID_STDOUT = "invalid-stdout"
_INVALID_STDERR = "invalid-stderr"
_INITIALIZE_DELAY_SECONDS = 0.5
_NEVER_ANSWERS_TIMEOUT_SECONDS = 1.0
_CONTEXT_LIMIT = 1007997
_SMALL_CONTEXT_LIMIT = 200000
_MODEL_LIST = [
    {"modelId": "muse-wonder-1.0", "variants": ["low", "high"], "contextLimit": _CONTEXT_LIMIT},
    {"modelId": "muse-spark-1.2", "variants": ["low"], "contextLimit": _SMALL_CONTEXT_LIMIT},
]
_MODE_ENV = "FAKE_MUSE_MODE"
_LOG_ENV = "FAKE_MUSE_LOG"
_DELAY_ENV = "FAKE_MUSE_INITIALIZE_DELAY_SECONDS"
_MODELS_ENV = "FAKE_MUSE_MODEL_LIST"
_EXECUTABLE_MODE = 0o755
_FAKE_MUSE = """
import json
import os
import sys
import threading

MODE = os.environ["FAKE_MUSE_MODE"]
LOG = os.environ["FAKE_MUSE_LOG"]
INITIALIZE_DELAY_SECONDS = float(os.environ["FAKE_MUSE_INITIALIZE_DELAY_SECONDS"])
MODEL_LIST = json.loads(os.environ["FAKE_MUSE_MODEL_LIST"])
lock = threading.Lock()
initialize_answered = False
initialized = False


def write(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\\n")
    sys.stdout.flush()


def send(message):
    with lock:
        write(message)


def answer_initialize(identifier):
    global initialize_answered
    with lock:
        initialize_answered = True
        write({"jsonrpc": "2.0", "id": identifier, "result": {"serverInfo": {"name": "muse"}}})


def receive(message):
    global initialized
    method = message.get("method")
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(method + "\\n")
    if method == "initialize":
        if MODE == "silent":
            return
        if MODE == "invalid-stdout":
            sys.stdout.buffer.write(b"\\xff\\n")
            sys.stdout.flush()
            return
        if MODE == "invalid-stderr":
            sys.stderr.buffer.write(b"\\xff")
            sys.stderr.flush()
        if MODE == "rejected":
            send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602}})
            return
        timer = threading.Timer(INITIALIZE_DELAY_SECONDS, answer_initialize, (message["id"],))
        timer.daemon = True
        timer.start()
    elif method == "initialized":
        with lock:
            initialized = initialize_answered
    elif method == "model/list":
        if initialized:
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"models": MODEL_LIST}})
        else:
            send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32600}})


for line in sys.stdin:
    if line.strip():
        receive(json.loads(line))
"""


def _install_fake_muse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: str) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / MUSE_COMMAND
    executable.write_text(f"#!{sys.executable}\n{_FAKE_MUSE}", encoding="utf-8")
    executable.chmod(_EXECUTABLE_MODE)
    log = tmp_path / "received.log"
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(_MODE_ENV, mode)
    monkeypatch.setenv(_LOG_ENV, str(log))
    monkeypatch.setenv(_DELAY_ENV, str(_INITIALIZE_DELAY_SECONDS))
    monkeypatch.setenv(_MODELS_ENV, json.dumps(_MODEL_LIST))
    return log


def _received_methods(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines()


async def test_model_list_is_requested_only_after_initialize_has_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _install_fake_muse(tmp_path, monkeypatch, mode=_ORDERED)

    catalog = await read_model_catalog(SubprocessRunner(), dict(os.environ))

    assert catalog.catalog.error is None
    assert catalog.catalog.models == (
        ModelChoice("muse-wonder-1.0", ("low", "high")),
        ModelChoice("muse-spark-1.2", ("low",)),
    )
    assert catalog.context_windows == {
        "muse-wonder-1.0": _CONTEXT_LIMIT,
        "muse-spark-1.2": _SMALL_CONTEXT_LIMIT,
    }
    assert _received_methods(log) == ["initialize", "initialized", "model/list"]


async def test_a_rejected_initialize_is_reported_and_no_model_list_is_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _install_fake_muse(tmp_path, monkeypatch, mode=_REJECTED)

    catalog = await read_model_catalog(SubprocessRunner(), dict(os.environ))

    assert catalog.catalog.error is not None
    assert catalog.catalog.models == ()
    assert _received_methods(log) == ["initialize"]


async def test_a_server_that_never_answers_is_stopped_at_the_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, mode=_SILENT)

    catalog = await read_model_catalog(
        SubprocessRunner(), dict(os.environ), timeout=_NEVER_ANSWERS_TIMEOUT_SECONDS
    )

    assert catalog.catalog.error is not None
    assert catalog.catalog.models == ()


@pytest.mark.parametrize("mode", [_INVALID_STDOUT, _INVALID_STDERR])
async def test_invalid_utf8_from_muse_returns_a_catalog_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _install_fake_muse(tmp_path, monkeypatch, mode=mode)

    catalog = await read_model_catalog(SubprocessRunner(), dict(os.environ))

    assert catalog.catalog.error is not None
    assert catalog.catalog.models == ()
