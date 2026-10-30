from __future__ import annotations

import pytest

from shikiru.dashboard.orchestration_fields import (
    MAX_CPU,
    MAX_PARALLEL,
    MIN_FREE_RAM,
    OrchestrationField,
    OrchestrationFieldError,
)


@pytest.mark.parametrize(
    ("field", "raw", "value"),
    [
        (MAX_PARALLEL, "1", 1),
        (MAX_PARALLEL, " 12 ", 12),
        (MAX_CPU, "0", 0),
        (MAX_CPU, "100", 100),
        (MIN_FREE_RAM, "0", 0),
        (MIN_FREE_RAM, "2.5", 2.5),
    ],
)
def test_accepts_values_at_each_field_boundary(
    field: OrchestrationField,
    raw: str,
    value: float,
) -> None:
    assert field.parse(raw) == value


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        (MAX_PARALLEL, "0"),
        (MAX_PARALLEL, "-2"),
        (MAX_PARALLEL, "2.0"),
        (MAX_PARALLEL, ""),
        (MAX_PARALLEL, "three"),
        (MAX_CPU, "101"),
        (MIN_FREE_RAM, "-0.5"),
        (MIN_FREE_RAM, "nan"),
    ],
)
def test_rejects_values_outside_each_field_rule_naming_its_input(
    field: OrchestrationField,
    raw: str,
) -> None:
    with pytest.raises(OrchestrationFieldError) as error:
        field.parse(raw)
    assert error.value.input_id == field.input_id
