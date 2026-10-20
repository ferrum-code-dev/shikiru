from __future__ import annotations

import json

import pytest

from shikiru.plugins.agents.claude.model_catalog import parse_model_catalog
from shikiru.plugins.agents.claude.options import MODEL_CATALOG_REQUEST_ID


def test_initialize_catalog_keeps_native_aliases_resolved_identifiers_and_efforts() -> None:
    output = "\n".join(
        json.dumps(message)
        for message in (
            {
                "type": "control_response",
                "response": {
                    "request_id": "other-request",
                    "subtype": "success",
                    "response": {"models": [{"value": "wrong"}]},
                },
            },
            {
                "type": "control_response",
                "response": {
                    "request_id": MODEL_CATALOG_REQUEST_ID,
                    "subtype": "success",
                    "response": {
                        "models": [
                            {
                                "value": "sonnet",
                                "resolvedModel": "claude-sonnet-4-5",
                                "supportsEffort": True,
                                "supportedEffortLevels": ["low", "high"],
                                "description": "not retained",
                            },
                            {"value": "fable", "supportsEffort": False},
                            {"value": "haiku", "supportsEffort": True},
                        ]
                    },
                },
            },
        )
    )

    catalog = parse_model_catalog(output, MODEL_CATALOG_REQUEST_ID)

    assert not catalog.complete
    assert [(choice.model, choice.efforts) for choice in catalog.models] == [
        ("sonnet", ("low", "high")),
        ("claude-sonnet-4-5", ("low", "high")),
        ("fable", ()),
        ("haiku", None),
    ]


@pytest.mark.parametrize(
    "output",
    [
        "not-json",
        json.dumps({"type": "control_response", "response": {"request_id": "other"}}),
        json.dumps(
            {
                "type": "control_response",
                "response": {
                    "request_id": MODEL_CATALOG_REQUEST_ID,
                    "subtype": "success",
                    "response": {"models": []},
                },
            }
        ),
    ],
)
def test_initialize_catalog_rejects_missing_or_malformed_native_choices(output: str) -> None:
    with pytest.raises(TypeError):
        parse_model_catalog(output, MODEL_CATALOG_REQUEST_ID)
