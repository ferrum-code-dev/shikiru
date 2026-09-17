"""Unit tests for the pure session-name rule (docs/design/06-operator.md §5.1)."""

from __future__ import annotations

import pytest

from shikiru.tmux.session import session_name


@pytest.mark.parametrize(
    ("project_name", "expected"),
    [
        ("ferrum", "[shikiru] ferrum"),
        ("api.gateway", "[shikiru] api_gateway"),
        ("host:proj", "[shikiru] host_proj"),
        ("a.b:c", "[shikiru] a_b_c"),
    ],
)
def test_session_name_replaces_dots_and_colons(project_name: str, expected: str) -> None:
    assert session_name(project_name) == expected
