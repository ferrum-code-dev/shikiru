"""Builds a task window's panes from `dashboard.layout` (06 §5.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.config.settings import LayoutSplit, PaneLeaf
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from shikiru.config.settings import LayoutNode, LayoutSplit
    from shikiru.tmux.client import Tmux

_FULL_PERCENT = 100
_RESTORE_SPLIT_PERCENT = 50
_VERTICAL_STACK_LAYOUT = "even-vertical"
_VERTICAL_AXIS = "v"
_MIN_STACK_PANES = 2


class LayoutBuilder:
    """Splits a window's base pane into the tree `dashboard.layout` describes."""

    def __init__(self, tmux: Tmux) -> None:
        """Split panes through `tmux`."""
        self._tmux = tmux

    async def build(
        self, base_pane_id: str, root: LayoutNode, *, cwd: Path | None = None
    ) -> dict[str, str]:
        """Build `root` starting from `base_pane_id`, returning `{pane name: pane id}`.

        Raises `ConfigError` if two leaves share a pane name.
        """
        panes: dict[str, str] = {}
        await self._place(base_pane_id, root, panes, cwd)
        return panes

    async def restore_missing(
        self,
        root: LayoutNode,
        existing: Mapping[str, str],
        *,
        cwd: Path,
    ) -> dict[str, str]:
        """Restore absent layout leaves beside surviving leaves and return their pane ids."""
        panes = dict(existing)
        await self._restore_node(root, panes, cwd)
        return panes

    async def restore_vertical_stack(
        self, window_id: str, root: LayoutSplit, panes: Mapping[str, str]
    ) -> None:
        """Reapply configured sizes to a direct vertical leaf stack without replacing panes."""
        if root.split != _VERTICAL_AXIS or not all(
            isinstance(child, PaneLeaf) for child in root.panes
        ):
            return
        leaves = [child for child in root.panes if isinstance(child, PaneLeaf)]
        if len(leaves) < _MIN_STACK_PANES:
            return
        await self._tmux.select_layout(window_id, _VERTICAL_STACK_LAYOUT)
        fractions = _resolve_fractions([leaf.size for leaf in leaves])
        available_height = sum(
            [int(await self._tmux.display(panes[leaf.pane], "#{pane_height}")) for leaf in leaves]
        )
        heights = _allocate_heights(fractions, available_height)
        for leaf, height in zip(leaves[:-1], heights[:-1], strict=True):
            await self._tmux.resize_pane_height(panes[leaf.pane], height)

    async def _restore_node(self, node: LayoutNode, panes: dict[str, str], cwd: Path) -> None:
        if isinstance(node, PaneLeaf):
            return
        for index, child in enumerate(node.panes):
            names = _leaf_names(child)
            if names.issubset(panes):
                if not isinstance(child, PaneLeaf):
                    await self._restore_node(child, panes, cwd)
                continue
            if names.intersection(panes):
                await self._restore_node(child, panes, cwd)
                continue
            target, before = _neighbor_pane(node.panes, index, panes)
            if target is None:
                message = "dashboard.layout cannot restore panes without a surviving neighbor"
                raise ConfigError(message)
            base_pane = await self._tmux.split_window(
                target,
                axis=node.split,
                size_pct=_RESTORE_SPLIT_PERCENT,
                cwd=cwd,
                detached=True,
                before=before,
            )
            await self._place(base_pane, child, panes, cwd)

    async def _place(
        self, pane_id: str, node: LayoutNode, panes: dict[str, str], cwd: Path | None
    ) -> None:
        if isinstance(node, PaneLeaf):
            if node.pane in panes:
                message = f"dashboard.layout has more than one pane named {node.pane!r}"
                raise ConfigError(message)
            panes[node.pane] = pane_id
            return
        await self._place_split(pane_id, node, panes, cwd)

    async def _place_split(
        self, pane_id: str, node: LayoutSplit, panes: dict[str, str], cwd: Path | None
    ) -> None:
        sizes = [child.size if isinstance(child, PaneLeaf) else None for child in node.panes]
        fractions = _resolve_fractions(sizes)
        remaining_fraction = 1.0
        current_pane_id = pane_id
        for child, fraction in zip(node.panes[:-1], fractions[:-1], strict=True):
            rest_fraction = remaining_fraction - fraction
            rest_pct = round(rest_fraction / remaining_fraction * _FULL_PERCENT)
            rest_pane_id = await self._tmux.split_window(
                current_pane_id,
                axis=node.split,
                size_pct=rest_pct,
                cwd=cwd,
                detached=True,
            )
            await self._place(current_pane_id, child, panes, cwd)
            current_pane_id = rest_pane_id
            remaining_fraction = rest_fraction
        await self._place(current_pane_id, node.panes[-1], panes, cwd)


def _leaf_names(node: LayoutNode) -> set[str]:
    if isinstance(node, PaneLeaf):
        return {node.pane}
    names: set[str] = set()
    for child in node.panes:
        names.update(_leaf_names(child))
    return names


def _neighbor_pane(
    siblings: list[LayoutNode], index: int, panes: Mapping[str, str]
) -> tuple[str | None, bool]:
    for sibling in reversed(siblings[:index]):
        names = _leaf_names(sibling).intersection(panes)
        if names:
            return panes[_last_leaf_name(sibling, names)], False
    for sibling in siblings[index + 1 :]:
        names = _leaf_names(sibling).intersection(panes)
        if names:
            return panes[_first_leaf_name(sibling, names)], True
    return None, False


def _first_leaf_name(node: LayoutNode, existing: set[str]) -> str:
    if isinstance(node, PaneLeaf):
        return node.pane
    for child in node.panes:
        names = _leaf_names(child).intersection(existing)
        if names:
            return _first_leaf_name(child, names)
    message = "dashboard.layout has no first surviving pane"
    raise ConfigError(message)


def _last_leaf_name(node: LayoutNode, existing: set[str]) -> str:
    if isinstance(node, PaneLeaf):
        return node.pane
    for child in reversed(node.panes):
        names = _leaf_names(child).intersection(existing)
        if names:
            return _last_leaf_name(child, names)
    message = "dashboard.layout has no last surviving pane"
    raise ConfigError(message)


def _resolve_fractions(sizes: list[int | None]) -> list[float]:
    explicit_total = sum(size for size in sizes if size is not None)
    if explicit_total > _FULL_PERCENT:
        message = f"dashboard.layout pane sizes add up to more than 100% ({explicit_total}%)"
        raise ConfigError(message)
    unsized_count = sizes.count(None)
    share = (_FULL_PERCENT - explicit_total) / unsized_count if unsized_count else 0.0
    return [(size if size is not None else share) / _FULL_PERCENT for size in sizes]


def _allocate_heights(fractions: list[float], total_height: int) -> list[int]:
    exact_heights = [fraction * total_height for fraction in fractions]
    heights = [int(height) for height in exact_heights]
    remaining = total_height - sum(heights)
    remainders = sorted(
        range(len(heights)),
        key=lambda index: exact_heights[index] - heights[index],
        reverse=True,
    )
    for index in remainders[:remaining]:
        heights[index] += 1
    return heights
