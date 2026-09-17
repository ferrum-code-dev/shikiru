"""Builds a task window's panes from `dashboard.layout` (06 §5.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.config.settings import LayoutSplit, PaneLeaf
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from shikiru.config.settings import LayoutNode
    from shikiru.tmux.client import Tmux

_FULL_PERCENT = 100


class LayoutBuilder:
    """Splits a window's base pane into the tree `dashboard.layout` describes."""

    def __init__(self, tmux: Tmux) -> None:
        """Split panes through `tmux`."""
        self._tmux = tmux

    async def build(self, base_pane_id: str, root: LayoutNode) -> dict[str, str]:
        """Build `root` starting from `base_pane_id`, returning `{pane name: pane id}`.

        Raises `ConfigError` if two leaves share a pane name.
        """
        panes: dict[str, str] = {}
        await self._place(base_pane_id, root, panes)
        return panes

    async def _place(self, pane_id: str, node: LayoutNode, panes: dict[str, str]) -> None:
        if isinstance(node, PaneLeaf):
            if node.pane in panes:
                message = f"dashboard.layout has more than one pane named {node.pane!r}"
                raise ConfigError(message)
            panes[node.pane] = pane_id
            return
        await self._place_split(pane_id, node, panes)

    async def _place_split(self, pane_id: str, node: LayoutSplit, panes: dict[str, str]) -> None:
        sizes = [child.size if isinstance(child, PaneLeaf) else None for child in node.panes]
        fractions = _resolve_fractions(sizes)
        remaining_fraction = 1.0
        current_pane_id = pane_id
        for child, fraction in zip(node.panes[:-1], fractions[:-1], strict=True):
            rest_fraction = remaining_fraction - fraction
            rest_pct = round(rest_fraction / remaining_fraction * _FULL_PERCENT)
            rest_pane_id = await self._tmux.split_window(
                current_pane_id, axis=node.split, size_pct=rest_pct
            )
            await self._place(current_pane_id, child, panes)
            current_pane_id = rest_pane_id
            remaining_fraction = rest_fraction
        await self._place(current_pane_id, node.panes[-1], panes)


def _resolve_fractions(sizes: list[int | None]) -> list[float]:
    explicit_total = sum(size for size in sizes if size is not None)
    if explicit_total > _FULL_PERCENT:
        message = f"dashboard.layout pane sizes add up to more than 100% ({explicit_total}%)"
        raise ConfigError(message)
    unsized_count = sizes.count(None)
    share = (_FULL_PERCENT - explicit_total) / unsized_count if unsized_count else 0.0
    return [(size if size is not None else share) / _FULL_PERCENT for size in sizes]
