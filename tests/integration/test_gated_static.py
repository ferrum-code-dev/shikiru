from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

from shikiru.ui.gated_static import GatedStatic
from tests.support.dashboard import WidgetHostApp
from tests.support.render import plain_text

if TYPE_CHECKING:
    from textual.widget import Widget

ONE_LINE = "alpha 1"
SAME_EXTENT_LINE = "alpha 2"
WIDER_LINE = "alpha 22"
TWO_LINES = "alpha 1\nbeta"


class LayoutCountingStatic(GatedStatic):
    def __init__(self, content: str) -> None:
        super().__init__(content, markup=False)
        self.layout_requests = 0

    def refresh(
        self,
        *regions: object,
        repaint: bool = True,
        layout: bool = False,
        recompose: bool = False,
    ) -> Widget:
        if layout:
            self.layout_requests += 1
        return super().refresh(*regions, repaint=repaint, layout=layout, recompose=recompose)  # type: ignore[arg-type]


async def test_identical_rendered_output_is_not_updated() -> None:
    static = LayoutCountingStatic(ONE_LINE)
    async with WidgetHostApp(static).run_test() as pilot:
        await pilot.pause()
        assert static.update_if_changed(Text(ONE_LINE)) is True
        await pilot.pause()
        shown = static.content
        requests = static.layout_requests

        assert static.update_if_changed(Text(ONE_LINE)) is False
        await pilot.pause()

        assert static.content is shown
        assert static.layout_requests == requests


async def test_changed_text_of_the_same_extent_repaints_without_a_layout() -> None:
    static = LayoutCountingStatic(ONE_LINE)
    async with WidgetHostApp(static).run_test() as pilot:
        await pilot.pause()
        static.update_if_changed(Text(ONE_LINE))
        await pilot.pause()
        requests = static.layout_requests

        assert static.update_if_changed(Text(SAME_EXTENT_LINE)) is True
        await pilot.pause()

        assert plain_text(static.content) == SAME_EXTENT_LINE
        assert static.layout_requests == requests


async def test_a_change_of_extent_still_requests_a_layout() -> None:
    static = LayoutCountingStatic(ONE_LINE)
    async with WidgetHostApp(static).run_test() as pilot:
        await pilot.pause()
        static.update_if_changed(Text(ONE_LINE))
        await pilot.pause()
        requests = static.layout_requests

        static.update_if_changed(Text(TWO_LINES))
        await pilot.pause()
        taller = static.layout_requests
        static.update_if_changed(Text(WIDER_LINE))
        await pilot.pause()

        assert taller > requests
        assert static.layout_requests > taller


async def test_an_unconditional_update_is_shown_even_when_the_output_matches() -> None:
    static = LayoutCountingStatic(ONE_LINE)
    async with WidgetHostApp(static).run_test() as pilot:
        await pilot.pause()
        static.update_if_changed(Text(ONE_LINE))
        static.update(Text(SAME_EXTENT_LINE))
        await pilot.pause()

        assert static.update_if_changed(Text(ONE_LINE)) is True


async def test_text_that_differs_only_in_styling_is_updated() -> None:
    static = LayoutCountingStatic(ONE_LINE)
    async with WidgetHostApp(static).run_test() as pilot:
        await pilot.pause()
        static.update_if_changed(Text(ONE_LINE))
        await pilot.pause()

        assert static.update_if_changed(Text(ONE_LINE, style="bold red")) is True


async def test_content_with_an_explicit_key_is_skipped_only_while_its_key_holds() -> None:
    static = LayoutCountingStatic(ONE_LINE)
    async with WidgetHostApp(static).run_test() as pilot:
        await pilot.pause()
        assert static.update_if_changed(Text(ONE_LINE), key="first") is True
        await pilot.pause()

        assert static.update_if_changed(Text(SAME_EXTENT_LINE), key="first") is False
        assert static.update_if_changed(Text(SAME_EXTENT_LINE), key="second") is True
