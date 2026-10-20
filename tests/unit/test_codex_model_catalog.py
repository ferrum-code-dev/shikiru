from __future__ import annotations

import json

import pytest

from shikiru.agents.contracts import ModelChoice
from shikiru.plugins.agents.codex.model_catalog import parse_bundled_model_choices

_DUPLICATE_EFFORT_CATALOG = json.dumps(
    {
        "models": [
            {
                "slug": "gpt-6-luna",
                "visibility": "list",
                "supported_in_api": True,
                "supported_reasoning_levels": [{"effort": "high"}, {"effort": "high"}],
            }
        ]
    }
)
_INVALID_EFFORT_CATALOG = json.dumps(
    {
        "models": [
            {
                "slug": "gpt-6-luna",
                "visibility": "list",
                "supported_in_api": True,
                "supported_reasoning_levels": "high",
            }
        ]
    }
)


def test_bundled_catalog_keeps_api_models_and_per_model_efforts() -> None:
    output = json.dumps(
        {
            "models": [
                {
                    "slug": "gpt-6-luna",
                    "visibility": "list",
                    "supported_in_api": True,
                    "context_window": 128000,
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                },
                {
                    "slug": "gpt-hidden",
                    "visibility": "hide",
                    "supported_in_api": True,
                    "context_window": 96000,
                    "supported_reasoning_levels": None,
                },
                {
                    "slug": "api-disabled",
                    "visibility": "list",
                    "supported_in_api": False,
                    "supported_reasoning_levels": [{"effort": "ultra"}],
                },
                {
                    "slug": "gpt-5.6-sol",
                    "visibility": "list",
                    "supported_in_api": True,
                },
            ]
        }
    )

    choices = parse_bundled_model_choices(output)

    assert choices == (
        ModelChoice(model="gpt-6-luna", efforts=("low", "high")),
        ModelChoice(model="gpt-hidden", efforts=None, offered=False),
        ModelChoice(model="gpt-5.6-sol", efforts=None),
    )
    assert tuple(choice.model for choice in choices if choice.offered) == (
        "gpt-6-luna",
        "gpt-5.6-sol",
    )


@pytest.mark.parametrize(
    "output",
    [
        "not-json",
        json.dumps([]),
        json.dumps({"models": "gpt-6-luna"}),
        json.dumps({"models": [{}]}),
        _DUPLICATE_EFFORT_CATALOG,
        _INVALID_EFFORT_CATALOG,
    ],
)
def test_malformed_bundled_catalog_is_not_interpreted_as_model_choices(output: str) -> None:
    with pytest.raises(TypeError):
        parse_bundled_model_choices(output)
