from __future__ import annotations

import pytest

from shikiru.agents.contracts import ModelCatalog, ModelChoice, assess_model_choice

_COMPLETE_CATALOG = ModelCatalog(
    models=(
        ModelChoice(model="gpt-6-luna", efforts=("low", "high")),
        ModelChoice(model="gpt-6-sol", efforts=("medium",)),
        ModelChoice(model="gpt-5.6-sol", efforts=None),
        ModelChoice(model="gpt-5.6-terra", efforts=()),
        ModelChoice(model="gpt-hidden", efforts=None, offered=False),
    ),
    complete=True,
)
_PARTIAL_CATALOG = ModelCatalog(
    models=(ModelChoice(model="gpt-6-luna", efforts=("low", "high")),),
)


@pytest.mark.parametrize(
    ("catalog", "model", "effort", "supported", "diagnostic"),
    [
        (_COMPLETE_CATALOG, "gpt-6-luna", None, True, "native effort default"),
        (_COMPLETE_CATALOG, "gpt-6-luna", "high", True, "supports effort 'high'"),
        (_COMPLETE_CATALOG, "GPT-6-Luna", None, False, "use 'gpt-6-luna'"),
        (_COMPLETE_CATALOG, "made-up", None, False, "available model identifiers"),
        (_COMPLETE_CATALOG, "gpt-6-sol", "high", False, "supported efforts: medium"),
        (_COMPLETE_CATALOG, "gpt-5.6-sol", "high", None, "could not be verified"),
        (_COMPLETE_CATALOG, "gpt-5.6-terra", "high", False, "no explicit effort choices"),
        (_COMPLETE_CATALOG, "gpt-hidden", None, True, "native effort default"),
        (_PARTIAL_CATALOG, "missing-model", None, None, "catalog is incomplete"),
        (
            ModelCatalog(error="native discovery failed"),
            "gpt-6-luna",
            None,
            None,
            "catalog unavailable",
        ),
    ],
)
def test_catalog_assessment_separates_supported_unsupported_and_unverified_choices(
    catalog: ModelCatalog,
    model: str,
    effort: str | None,
    supported: bool | None,
    diagnostic: str,
) -> None:
    assessment = assess_model_choice(model, effort, catalog)

    assert assessment.supported is supported
    assert diagnostic in assessment.detail
    if model == "gpt-6-sol":
        assert "gpt-6-luna" not in assessment.detail
