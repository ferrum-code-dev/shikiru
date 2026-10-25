"""Integration tests for `LayoutBuilder` on the private `tmux_server`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.config.settings import LayoutSplit, PaneLeaf
from shikiru.errors import ConfigError
from shikiru.tmux.client import Tmux
from shikiru.tmux.layout import LayoutBuilder

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer

_SIZE_TOLERANCE_PCT = 3
_WINDOW_WIDTH = 200
_WINDOW_HEIGHT = 50

_TREE = LayoutSplit(
    split="h",
    panes=[
        PaneLeaf(pane="system", size=30),
        LayoutSplit(
            split="v",
            panes=[
                PaneLeaf(pane="leader"),
                PaneLeaf(pane="implementer"),
                PaneLeaf(pane="shell", size=20),
            ],
        ),
    ],
)


async def _new_window(tmux: Tmux) -> str:
    return await tmux.new_window("=test", name="layout", command=("sh", "-c", "sleep 300"))


async def test_build_produces_one_pane_per_leaf_with_no_duplicates(
    tmux_server: TmuxServer,
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    base_pane = await _new_window(tmux)

    panes = await LayoutBuilder(tmux).build(base_pane, _TREE)

    assert set(panes) == {"system", "leader", "implementer", "shell"}
    assert len(set(panes.values())) == len(panes)


async def test_build_respects_configured_and_equal_share_sizes(tmux_server: TmuxServer) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    base_pane = await _new_window(tmux)

    panes = await LayoutBuilder(tmux).build(base_pane, _TREE)

    widths = {
        role: int(await tmux.display(pane_id, "#{pane_width}")) for role, pane_id in panes.items()
    }
    heights = {
        role: int(await tmux.display(pane_id, "#{pane_height}")) for role, pane_id in panes.items()
    }

    assert widths["system"] == pytest.approx(_WINDOW_WIDTH * 0.30, abs=_WINDOW_WIDTH * 0.03)
    rest_width = _WINDOW_WIDTH - widths["system"] - 1
    assert widths["leader"] == pytest.approx(rest_width, abs=_WINDOW_WIDTH * 0.03)
    assert widths["implementer"] == pytest.approx(rest_width, abs=_WINDOW_WIDTH * 0.03)
    assert widths["shell"] == pytest.approx(rest_width, abs=_WINDOW_WIDTH * 0.03)

    assert heights["shell"] == pytest.approx(_WINDOW_HEIGHT * 0.20, abs=_WINDOW_HEIGHT * 0.05)
    remaining_height = _WINDOW_HEIGHT - heights["shell"] - 2
    assert heights["leader"] == pytest.approx(
        remaining_height / 2, abs=_WINDOW_HEIGHT * _SIZE_TOLERANCE_PCT / 100
    )
    assert heights["implementer"] == pytest.approx(
        remaining_height / 2, abs=_WINDOW_HEIGHT * _SIZE_TOLERANCE_PCT / 100
    )


async def test_build_places_panes_in_configured_order(tmux_server: TmuxServer) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    base_pane = await _new_window(tmux)

    panes = await LayoutBuilder(tmux).build(base_pane, _TREE)

    positions = {
        role: (
            int(await tmux.display(pane_id, "#{pane_left}")),
            int(await tmux.display(pane_id, "#{pane_top}")),
        )
        for role, pane_id in panes.items()
    }

    assert positions["system"][0] < positions["leader"][0]
    assert positions["system"][0] < positions["implementer"][0]
    assert positions["system"][0] < positions["shell"][0]
    assert positions["leader"][0] == positions["implementer"][0] == positions["shell"][0]
    assert positions["leader"][1] < positions["implementer"][1] < positions["shell"][1]


async def test_build_rejects_duplicate_leaf_pane_names(tmux_server: TmuxServer) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    base_pane = await _new_window(tmux)
    duplicate_tree = LayoutSplit(
        split="h",
        panes=[PaneLeaf(pane="leader"), PaneLeaf(pane="leader")],
    )

    with pytest.raises(ConfigError):
        await LayoutBuilder(tmux).build(base_pane, duplicate_tree)


async def test_build_rejects_sizes_that_add_up_to_more_than_100_percent(
    tmux_server: TmuxServer,
) -> None:
    tmux = Tmux(tmux_server.runner, socket=tmux_server.socket_name)
    base_pane = await _new_window(tmux)
    oversized_tree = LayoutSplit(
        split="h",
        panes=[PaneLeaf(pane="system", size=60), PaneLeaf(pane="shell", size=60)],
    )

    with pytest.raises(ConfigError):
        await LayoutBuilder(tmux).build(base_pane, oversized_tree)
