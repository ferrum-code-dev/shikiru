"""Integration tests for `ContextWindowStore`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.store.context_windows import ContextWindowStore

if TYPE_CHECKING:
    from tests.support.store import Connection

_AGENT = "claude"
_MODEL = "claude-haiku-5-5"


def test_the_latest_window_reported_for_a_model_replaces_the_earlier_one(
    connection: Connection,
) -> None:
    store = ContextWindowStore(connection)
    store.record(_AGENT, _MODEL, 200_000)

    store.record(_AGENT, _MODEL, 1_000_000)

    assert store.window(_AGENT, _MODEL) == 1_000_000


def test_windows_are_kept_per_agent_and_per_model(connection: Connection) -> None:
    store = ContextWindowStore(connection)
    store.record(_AGENT, _MODEL, 1_000_000)

    assert store.window("claude-work", _MODEL) is None
    assert store.window(_AGENT, "claude-sonnet-5") is None
