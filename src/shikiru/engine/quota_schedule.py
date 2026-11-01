"""What the engine will next do about each quota-blocked agent, for the views that show it."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shikiru.domain.quota_waits import ScheduledQuotaAction


class QuotaSchedule:
    """The next scheduled action per agent, in memory: it is rebuilt as the engine acts again."""

    def __init__(self) -> None:
        """Start with no action scheduled."""
        self._actions: dict[str, ScheduledQuotaAction] = {}

    def record(self, agent: str, action: ScheduledQuotaAction) -> None:
        """Remember `action` as `agent`'s next one."""
        self._actions[agent] = action

    def get(self, agent: str) -> ScheduledQuotaAction | None:
        """Return `agent`'s recorded action, or none when none was recorded."""
        return self._actions.get(agent)
