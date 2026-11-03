"""Decide whether a merged pull request's task branch holds feedback work nobody published."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.engine.integration import PUBLISHED_TIP_KEY
from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.tasks import Task
    from shikiru.store.events import EventLog
    from shikiru.store.feedback import FeedbackStore
    from shikiru.workspace.git import Git
    from shikiru.workspace.github import GitHub

_OPENED_TIP_KEY = "branch_sha"
_BRANCH_REF = "refs/heads/{branch}"
_LOGGER = logging.getLogger(__name__)


class FeedbackWorkAudit:
    """Tell, from recorded publication evidence, whether a task tip may be discarded."""

    def __init__(
        self,
        *,
        git: Git,
        github: GitHub,
        events: EventLog,
        store: FeedbackStore,
        project_root: Path,
    ) -> None:
        """Read the task branch of `project_root`, events, feedback rounds and the PR head."""
        self._git = git
        self._github = github
        self._events = events
        self._store = store
        self._project_root = project_root

    async def holds_unpublished_work(self, task: Task) -> bool:
        """Return whether the branch of `task` may hold feedback commits the PR never received.

        Only a task with a persisted feedback round can; the round row, not its event, is the
        evidence that one started. Its current branch tip must be a
        tip the engine recorded as pushed (when the PR opened or a round published), or the
        head the pull request ended with. A merge style that breaks ancestry is therefore
        irrelevant, and a push whose checkpoint was lost is settled by the PR head or else kept.
        """
        repo = task.repo
        if repo is None:
            return False
        url = self._store.last_pull_request_url(task.id)
        if url is None:
            return False
        recorded = self._events.since(
            0, kinds=[EventKind.PR_OPENED, EventKind.FEEDBACK_PUBLISHED], task_id=task.id
        )
        ref = _BRANCH_REF.format(branch=repo.branch)
        if not await self._git.branch_exists(self._project_root, repo.branch):
            return False
        tip = await self._git.rev_parse(self._project_root, ref)
        pushed = {
            str(event.data[key])
            for event in recorded
            for key in (_OPENED_TIP_KEY, PUBLISHED_TIP_KEY)
            if key in event.data
        }
        if tip in pushed:
            return False
        try:
            feedback = await self._github.pull_request_feedback(self._project_root, url)
        except ShikiruError:
            _LOGGER.warning("keeping %s: the head of %s could not be read", repo.branch, url)
            return True
        return feedback.head_sha != tip
