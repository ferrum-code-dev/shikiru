"""Muse model catalog parsing from `muse serve` JSON-RPC replies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shikiru.agents.contracts import RUNTIME_DEFAULT_MODEL, ModelChoice
from shikiru.errors import ShikiruError
from shikiru.plugins.agents.muse.catalog import parse_model_catalog

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "agents" / "muse" / "model_list.jsonl"
_INITIALIZE_REPLY = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {}}})
_NOTIFICATION = json.dumps({"jsonrpc": "2.0", "method": "usage/changed", "params": {}})
_REJECTION = {"code": -32600, "message": "Not initialized", "data": {"kind": "notInitialized"}}
_CONTEXT_LIMIT = 1007997
_SMALLER_CONTEXT_LIMIT = 200000


def _model(
    identifier: object = "muse-wonder-1.0",
    variants: object = ("low", "high"),
    context_limit: object = _CONTEXT_LIMIT,
) -> dict[str, Any]:
    return {
        "modelId": identifier,
        "variants": variants,
        "contextLimit": context_limit,
        "isDefault": True,
    }


def _model_list_reply(models: object) -> str:
    result = {"providerId": "meta", "source": "providerCatalog", "models": models}
    return json.dumps({"jsonrpc": "2.0", "id": 2, "result": result})


def _output(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def test_captured_model_list_becomes_choices_with_their_variants_and_context_windows() -> None:
    parsed = parse_model_catalog(_FIXTURE.read_text(encoding="utf-8"))

    assert parsed.catalog.models == (
        ModelChoice("muse-wonder-1.0", ("minimal", "low", "medium", "high", "xhigh", "max")),
        ModelChoice("muse-ember-1.0", ("minimal", "low", "medium", "high", "xhigh", "max")),
        ModelChoice("muse-spark-1.3", ("minimal", "low", "medium", "high", "xhigh", "max")),
        ModelChoice("muse-spark-1.2", ("minimal", "low", "medium", "high", "xhigh")),
    )
    assert parsed.context_windows == {
        choice.model: _CONTEXT_LIMIT for choice in parsed.catalog.models
    }


def test_the_catalog_declares_runtime_default_support_without_a_default_choice() -> None:
    parsed = parse_model_catalog(_FIXTURE.read_text(encoding="utf-8"))

    assert parsed.catalog.supports_runtime_default
    assert RUNTIME_DEFAULT_MODEL not in {choice.model for choice in parsed.catalog.models}


def test_each_model_keeps_its_own_context_window() -> None:
    output = _output(
        _INITIALIZE_REPLY,
        _model_list_reply(
            [
                _model("muse-wonder-1.0", context_limit=_CONTEXT_LIMIT),
                _model("muse-spark-1.2", context_limit=_SMALLER_CONTEXT_LIMIT),
            ]
        ),
    )

    assert parse_model_catalog(output).context_windows == {
        "muse-wonder-1.0": _CONTEXT_LIMIT,
        "muse-spark-1.2": _SMALLER_CONTEXT_LIMIT,
    }


def test_notifications_and_blank_lines_between_replies_are_skipped() -> None:
    output = _output(
        _NOTIFICATION, _INITIALIZE_REPLY, "", _NOTIFICATION, _model_list_reply([_model()])
    )

    assert parse_model_catalog(output).catalog.models == (
        ModelChoice("muse-wonder-1.0", ("low", "high")),
    )


@pytest.mark.parametrize(
    "output",
    [
        pytest.param(_output(_model_list_reply([_model()])), id="no-initialize-reply"),
        pytest.param(_output(_INITIALIZE_REPLY), id="no-model-list-reply"),
        pytest.param(
            _output(
                json.dumps({"jsonrpc": "2.0", "id": 1, "error": _REJECTION}),
                _model_list_reply([_model()]),
            ),
            id="initialize-rejected",
        ),
        pytest.param(
            _output(
                _INITIALIZE_REPLY,
                json.dumps({"jsonrpc": "2.0", "id": 2, "error": _REJECTION}),
            ),
            id="model-list-rejected",
        ),
        pytest.param(_output(_INITIALIZE_REPLY, _model_list_reply([])), id="empty-bundled-catalog"),
        pytest.param(
            _output(
                _INITIALIZE_REPLY,
                json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"providerId": "meta"}}),
            ),
            id="model-list-without-models",
        ),
        pytest.param(_output("not json"), id="unreadable-line"),
        pytest.param(_output("[]"), id="non-object-line"),
    ],
)
def test_replies_without_an_authoritative_model_list_are_rejected(output: str) -> None:
    with pytest.raises(ShikiruError):
        parse_model_catalog(output)


@pytest.mark.parametrize(
    "models",
    [
        pytest.param("muse-wonder-1.0", id="models-not-a-list"),
        pytest.param([_model(identifier="")], id="blank-model-id"),
        pytest.param([_model(identifier=7)], id="non-string-model-id"),
        pytest.param([_model(variants=None)], id="variants-missing"),
        pytest.param([_model(variants=["low", ""])], id="blank-effort"),
        pytest.param([_model(variants=["low", 3])], id="non-string-effort"),
        pytest.param([_model(variants=["low", "low"])], id="duplicate-effort"),
        pytest.param([_model(), _model()], id="duplicate-model"),
        pytest.param(["muse-wonder-1.0"], id="model-not-an-object"),
    ],
)
def test_model_entries_that_cannot_be_read_reject_the_whole_catalog(models: object) -> None:
    with pytest.raises(ShikiruError):
        parse_model_catalog(_output(_INITIALIZE_REPLY, _model_list_reply(models)))


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(
            {"modelId": "muse-wonder-1.0", "variants": ["low"]}, id="context-limit-missing"
        ),
        pytest.param(_model(context_limit=None), id="context-limit-null"),
        pytest.param(_model(context_limit=0), id="context-limit-zero"),
        pytest.param(_model(context_limit=-1), id="context-limit-negative"),
        pytest.param(_model(context_limit=True), id="context-limit-boolean"),
        pytest.param(_model(context_limit="1007997"), id="context-limit-string"),
        pytest.param(_model(context_limit=1.5), id="context-limit-fraction"),
    ],
)
def test_models_without_a_positive_context_limit_reject_the_whole_catalog(
    model: dict[str, Any],
) -> None:
    with pytest.raises(ShikiruError):
        parse_model_catalog(_output(_INITIALIZE_REPLY, _model_list_reply([model])))
