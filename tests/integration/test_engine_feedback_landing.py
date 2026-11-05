"""PR feedback rounds end to end: fake `gh`, a bare remote and fake agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.domain.events import EventKind
from shikiru.domain.tasks import EscalationReason, Status
from shikiru.paths import task_root
from tests.support.git_repo import COMMIT_IDENTITY_ARGS
from tests.support.github import (
    MergeStyle,
)

if TYPE_CHECKING:
    from tests.support.engine import EngineEnv
    from tests.support.github import FakeGitHub
    from tests.support.tmux import TmuxServer

from tests.support.feedback_rounds import (
    LOCAL_FILE,
    git,
    land_remotely,
    lose_checkpoint_of,
    on_remote_base,
    open_pull,
    publish_round,
    script_round,
    task_state,
)


@pytest.mark.parametrize("lost_start_event", [False, True], ids=["recorded", "start-event-lost"])
async def test_a_pull_request_merged_before_feedback_publication_keeps_the_local_feedback_commit(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
    lost_start_event: bool,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    published = await pull.remote_branch()
    await script_round(pull, tmux_server)
    await lose_checkpoint_of(pull, "verified_sha")
    stranded = await git(engine_env.project_root, "rev-parse", pull.task.repo.branch)  # type: ignore[union-attr]
    assert stranded != published
    await land_remotely(pull, MergeStyle.MERGE_COMMIT)
    if lost_start_event:
        engine_env.connection.execute(
            "DELETE FROM events WHERE kind = ?", (EventKind.FEEDBACK_STARTED.value,)
        )
    pull.retry(EscalationReason.ENGINE_ERROR)
    await pull.until(pull.flow.done)

    done = engine_env.tasks.require(pull.task.id)
    assert done.status is Status.DONE
    [recorded] = pull.events.since(0, kinds=[EventKind.TASK_DONE], task_id=pull.task.id)
    assert not any(stranded[:7] in commit for commit in recorded.data["commits"])
    assert await pull.remote_branch() == published
    assert pull.replies() == []
    assert not await on_remote_base(pull, stranded)
    assert await task_state(pull) == (True, stranded)

    await pull.restart()
    await pull.cleanup(done)
    await pull.cleanup(done)
    assert await task_state(pull) == (True, stranded)
    assert len(pull.events.since(0, kinds=[EventKind.TASK_DONE], task_id=pull.task.id)) == 1


async def test_a_branch_advanced_after_verification_is_kept_when_the_pull_request_merges(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await publish_round(pull, tmux_server)
    worktree = task_root(engine_env.project_root, pull.task.id)
    (worktree / LOCAL_FILE).write_text("later\n")
    await git(worktree, "add", LOCAL_FILE)
    await git(worktree, *COMMIT_IDENTITY_ARGS, "commit", "-m", "later work")
    advanced = await git(worktree, "rev-parse", "HEAD")
    await land_remotely(pull, MergeStyle.FAST_FORWARD)
    await pull.until(pull.flow.done)

    assert engine_env.tasks.require(pull.task.id).status is Status.DONE
    assert await task_state(pull) == (True, advanced)


@pytest.mark.parametrize("head_reported", [True, False], ids=["head-proves-it", "unproven"])
async def test_a_push_without_its_checkpoint_is_kept_unless_the_pull_request_head_proves_it(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
    head_reported: bool,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await script_round(pull, tmux_server)
    await lose_checkpoint_of(pull, "published_sha")
    pushed = await pull.remote_branch()
    local = await git(engine_env.project_root, "rev-parse", pull.task.repo.branch)  # type: ignore[union-attr]
    assert pushed == local
    assert not pull.events.since(0, kinds=[EventKind.FEEDBACK_PUBLISHED], task_id=pull.task.id)
    await land_remotely(pull, MergeStyle.FAST_FORWARD)
    if head_reported:
        fake_github.set_fields(1, headRefOid=pushed)
    pull.retry(EscalationReason.ENGINE_ERROR)
    await pull.until(pull.flow.done)

    assert engine_env.tasks.require(pull.task.id).status is Status.DONE
    present, branch = await task_state(pull)
    assert (present, branch) == ((False, None) if head_reported else (True, local))


async def test_a_squashed_pull_request_with_published_feedback_is_cleaned_up(
    engine_env: EngineEnv,
    fake_github: FakeGitHub,
    tmux_server: TmuxServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pull = await open_pull(engine_env, fake_github, tmp_path_factory)
    await publish_round(pull, tmux_server)
    await land_remotely(pull, MergeStyle.SQUASH)
    await pull.until(pull.flow.done)

    assert engine_env.tasks.require(pull.task.id).status is Status.DONE
    assert await task_state(pull) == (False, None)
