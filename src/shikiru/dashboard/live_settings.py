"""The project settings a running dashboard applies, reloaded when the engine starts a new run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from shikiru.config.settings import Settings
    from shikiru.store.reader import StateReader

LATEST_ENGINE_START_LIMIT = 1
NO_ENGINE_START = 0


class LiveSettings:
    """Hold the settings a dashboard reads, reloading them after each new engine start."""

    def __init__(self, state: StateReader, load: Callable[[], Settings]) -> None:
        """Record the latest engine start before loading, so a start during the load reloads."""
        self._state = state
        self._load = load
        self._run_id = self._latest_run_id()
        self._settings = load()
        self._checked_version: int | None = None
        self._reload_error: str | None = None

    def current(self) -> Settings:
        """Return the applied settings, reloading them first if a new engine run has started."""
        self._reload_if_engine_started()
        return self._settings

    def reload_error(self) -> str | None:
        """Return why the latest reload failed, or None when the applied settings are current."""
        self._reload_if_engine_started()
        return self._reload_error

    def _reload_if_engine_started(self) -> None:
        version = self._state.data_version()
        if version == self._checked_version:
            return
        self._checked_version = version
        run_id = self._latest_run_id()
        if run_id in (NO_ENGINE_START, self._run_id):
            return
        self._run_id = run_id
        try:
            self._settings = self._load()
        except ConfigError as error:
            self._reload_error = str(error)
        else:
            self._reload_error = None

    def _latest_run_id(self) -> int:
        started = self._state.latest_events(
            LATEST_ENGINE_START_LIMIT, kinds=(EventKind.ENGINE_STARTED,)
        )
        return started[0].id if started else NO_ENGINE_START
