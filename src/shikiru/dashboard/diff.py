"""The task Git diff popup: net or per-commit file patches with fullscreen."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Rule, Select, Static

from shikiru.dashboard.contract import DIFF_ALL_COMMITS
from shikiru.domain.diff import DiffLineKind, classify_diff_lines
from shikiru.ui.keys import key_hint
from shikiru.ui.popup import (
    POPUP_CLOSE_CLASS,
    POPUP_COMPACT_CONTROL_CSS,
    POPUP_CSS,
    POPUP_FRAME_CLASS,
    POPUP_HEADER_CLASS,
    POPUP_ROW_CLASS,
    POPUP_RULE_CLASS,
    POPUP_SECONDARY_CLASS,
    POPUP_TITLE_CLASS,
    popup_close_label,
    prepare_popup,
)
from shikiru.ui.tokens import DUSTY_ROSE, PRIMARY_TEXT_COLOR, SAGE, STEEL_BLUE, STYLE_BOLD

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType

    from shikiru.dashboard.contract import DiffDataView
    from shikiru.dashboard.services import TaskDiffService

DIFF_FRAME = "diff-frame"
DIFF_TITLE = "diff-title"
DIFF_CLOSE = "diff-close"
DIFF_COMMIT_SELECT = "diff-commit-select"
DIFF_FILE_SELECT = "diff-file-select"
DIFF_SCOPE = "diff-scope"
DIFF_SCROLL = "diff-scroll"
DIFF_TEXT = "diff-text"
DIFF_FOOTER = "diff-footer"
DIFF_FULLSCREEN = "diff-fullscreen"
DIFF_ALL_CHANGES = "All changes"
DIFF_COMMIT_CAPTION = "Commit"
DIFF_FILE_CAPTION = "File"
DIFF_LOADING = "Loading diff…"
DIFF_NO_FILES = "No files"
DIFF_NO_CHANGES = "No changes in this view."
DIFF_NO_TEXTUAL = "No textual diff for {path}."
DIFF_FULLSCREEN_LABEL = "f Fullscreen"
DIFF_RESTORE_LABEL = "f Restore"
DIFF_ESCAPE = "escape"
DIFF_FULLSCREEN_KEY = "f"
DIFF_CLOSE_ACTION = "close"
DIFF_FULLSCREEN_ACTION = "toggle_fullscreen"
DIFF_FRAME_FULLSCREEN_CLASS = "fullscreen"
DIFF_MESSAGE_CLASS = "message"
_NO_FILE = ""
_LOADING_FILE = "\0"
_LIVE_ALL_SCOPE = "Changes so far · includes uncommitted changes"
_LIVE_COMMIT_SCOPE = "Changes so far · committed only (uncommitted changes not included)"
_LANDED_SCOPE = "Landed as {integration}"
_LANDED_FALLBACK = "Landed"
_INTEGRATION_LABELS = {
    "merge": "merge",
    "squash": "squash",
    "pr": "pull request",
}
_LINE_BREAK = "\n"
_ESCAPE_CHARACTER = "\x1b"
_VISIBLE_ESCAPE = "\N{SYMBOL FOR ESCAPE}"
_LINE_STYLES = {
    DiffLineKind.ADDED: SAGE,
    DiffLineKind.REMOVED: DUSTY_ROSE,
    DiffLineKind.HUNK: f"{STYLE_BOLD} {STEEL_BLUE}",
    DiffLineKind.CONTEXT: PRIMARY_TEXT_COLOR,
}


def render_patch(patch: str) -> Text:
    """Render a Git patch with added, removed, hunk and context colours.

    Lines break only where Git breaks them, and escape characters of the file content are shown
    rather than sent to the terminal.
    """
    lines = patch.removesuffix(_LINE_BREAK).split(_LINE_BREAK)
    return Text(_LINE_BREAK).join(
        Text(line.replace(_ESCAPE_CHARACTER, _VISIBLE_ESCAPE), style=_LINE_STYLES[kind])
        for line, kind in zip(lines, classify_diff_lines(lines), strict=True)
    )


def scope_text(data: DiffDataView) -> str:
    """Return the scope line for `data`: changes so far or landed as its integration."""
    if data.live:
        return _LIVE_ALL_SCOPE if data.includes_uncommitted else _LIVE_COMMIT_SCOPE
    if data.integration is None:
        return _LANDED_FALLBACK
    return _LANDED_SCOPE.format(
        integration=_INTEGRATION_LABELS.get(data.integration, data.integration)
    )


class DiffScreen(ModalScreen[None]):
    """Show one task's net or per-commit Git diff with fullscreen."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(DIFF_ESCAPE, DIFF_CLOSE_ACTION, "Back"),
        Binding(DIFF_FULLSCREEN_KEY, DIFF_FULLSCREEN_ACTION, "Fullscreen", show=False),
    ]
    DEFAULT_CSS = (
        POPUP_CSS
        + POPUP_COMPACT_CONTROL_CSS
        + """
    #diff-frame { height: 1fr; max-height: 32; max-width: 80; }
    #diff-frame.fullscreen { max-height: 100%; max-width: 100%; margin: 0; padding: 0 1; }
    #diff-scope { width: 100%; height: auto; }
    #diff-scroll { height: 1fr; overflow: auto auto;
        scrollbar-size-vertical: $scrollbar-width;
        scrollbar-size-horizontal: $scrollbar-width; }
    #diff-text { width: auto; height: auto; text-wrap: nowrap; }
    #diff-text.message { width: 100%; text-wrap: wrap; }
    #diff-footer { width: 100%; height: 1; }
    #diff-footer Button { width: auto; min-width: 1; height: 1; margin: 0; padding: 0 1;
        border: none; background: $surface; text-style: none; }
    #diff-footer Button:focus { text-style: reverse; border: none; }
    """
    )

    def __init__(self, service: TaskDiffService, task_id: str) -> None:
        super().__init__()
        prepare_popup(self)
        self._service = service
        self._task_id = task_id
        self._commit = DIFF_ALL_COMMITS
        self._file: str | None = None
        self._generation = 0

    def compose(self) -> ComposeResult:
        with Vertical(id=DIFF_FRAME, classes=POPUP_FRAME_CLASS):
            with Horizontal(classes=POPUP_HEADER_CLASS):
                yield Label(f"Diff · {self._task_id}", id=DIFF_TITLE, classes=POPUP_TITLE_CLASS)
                yield Button(popup_close_label(), id=DIFF_CLOSE, classes=POPUP_CLOSE_CLASS)
            yield Rule(classes=POPUP_RULE_CLASS)
            with Horizontal(classes=POPUP_ROW_CLASS):
                yield Label(DIFF_COMMIT_CAPTION)
                yield Select(
                    ((DIFF_ALL_CHANGES, DIFF_ALL_COMMITS),),
                    value=DIFF_ALL_COMMITS,
                    allow_blank=False,
                    id=DIFF_COMMIT_SELECT,
                )
            with Horizontal(classes=POPUP_ROW_CLASS):
                yield Label(DIFF_FILE_CAPTION)
                yield Select(
                    ((DIFF_LOADING, _LOADING_FILE),),
                    value=_LOADING_FILE,
                    allow_blank=False,
                    disabled=True,
                    id=DIFF_FILE_SELECT,
                )
            yield Label("", id=DIFF_SCOPE, classes=POPUP_SECONDARY_CLASS, markup=False)
            with ScrollableContainer(id=DIFF_SCROLL, classes=POPUP_SECONDARY_CLASS):
                yield Static(DIFF_LOADING, id=DIFF_TEXT, classes=DIFF_MESSAGE_CLASS, markup=False)
            yield Rule(classes=POPUP_RULE_CLASS)
            with Horizontal(id=DIFF_FOOTER):
                yield Button(key_hint(DIFF_FULLSCREEN_LABEL), id=DIFF_FULLSCREEN)

    def on_mount(self) -> None:
        self.query_one(f"#{DIFF_COMMIT_SELECT}").focus()
        self._reload()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        if action == DIFF_FULLSCREEN_ACTION:
            return not any(select.expanded for select in self.query(Select[str]))
        return True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == DIFF_CLOSE:
            self.dismiss()
        elif event.button.id == DIFF_FULLSCREEN:
            self.action_toggle_fullscreen()

    def on_select_changed(self, event: Select.Changed) -> None:
        value = event.value
        if not isinstance(value, str):
            return
        if event.select.id == DIFF_COMMIT_SELECT and value != self._commit:
            self._commit = value
            self._file = None
            self._reload()
        elif event.select.id == DIFF_FILE_SELECT and value not in (self._file, _LOADING_FILE):
            self._file = value
            self._reload()

    def action_close(self) -> None:
        self.dismiss()

    def action_toggle_fullscreen(self) -> None:
        """Expand to the whole screen, or restore the popup size."""
        frame = self.query_one(f"#{DIFF_FRAME}")
        frame.toggle_class(DIFF_FRAME_FULLSCREEN_CLASS)
        expanded = frame.has_class(DIFF_FRAME_FULLSCREEN_CLASS)
        label = DIFF_RESTORE_LABEL if expanded else DIFF_FULLSCREEN_LABEL
        self.query_one(f"#{DIFF_FULLSCREEN}", Button).label = key_hint(label)

    def _reload(self) -> None:
        self._generation += 1
        self.run_worker(self._load(self._generation))

    async def _load(self, generation: int) -> None:
        data = await self._service.load(self._task_id, self._commit, self._file)
        if generation == self._generation and self.is_mounted:
            self._paint(data)

    def _paint(self, data: DiffDataView) -> None:
        self._paint_commits(data)
        self._paint_files(data)
        self.query_one(f"#{DIFF_SCOPE}", Label).update(scope_text(data))
        body = self.query_one(f"#{DIFF_TEXT}", Static)
        message = self._message(data)
        body.set_class(message is not None, DIFF_MESSAGE_CLASS)
        body.update(render_patch(data.patch) if message is None else message)
        self.query_one(f"#{DIFF_SCROLL}", ScrollableContainer).scroll_home(animate=False)

    def _message(self, data: DiffDataView) -> str | None:
        if data.error is not None:
            return data.error
        if self._file is None:
            return DIFF_NO_CHANGES
        return None if data.patch else DIFF_NO_TEXTUAL.format(path=self._file)

    def _paint_commits(self, data: DiffDataView) -> None:
        select = cast("Select[str]", self.query_one(f"#{DIFF_COMMIT_SELECT}", Select))
        options = [(Text(DIFF_ALL_CHANGES), DIFF_ALL_COMMITS)]
        options.extend((Text(f"{item.sha} {item.subject}"), item.sha) for item in data.commits)
        if self._commit not in {value for _, value in options}:
            options.append((Text(self._commit), self._commit))
        with select.prevent(Select.Changed):
            select.set_options(options)
            select.value = self._commit

    def _paint_files(self, data: DiffDataView) -> None:
        select = cast("Select[str]", self.query_one(f"#{DIFF_FILE_SELECT}", Select))
        if self._file not in data.files:
            self._file = next(iter(data.files), None)
        options = [(Text(path), path) for path in data.files]
        with select.prevent(Select.Changed):
            select.set_options(options or [(Text(DIFF_NO_FILES), _NO_FILE)])
            select.value = _NO_FILE if self._file is None else self._file
        select.disabled = self._file is None
