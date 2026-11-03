"""Run one PR feedback round: analyse, rework, verify, publish and reply, resumably."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.events import EventKind
from shikiru.domain.feedback import (
    FeedbackInput,
    FeedbackSourceKind,
    FeedbackStep,
    next_feedback_step,
    operator_questions,
    publishable_replies,
)
from shikiru.domain.feedback_rounds import (
    SHORT_SHA_LENGTH,
    Alignment,
    FeedbackReply,
    FeedbackRound,
    FeedbackStage,
    PublicationAction,
    ReplyState,
    align_with_remote,
    describe_checks,
    fixed_source_ids,
    plan_publication,
    reply_body,
    reply_marker,
)
from shikiru.domain.reports import (
    DECISION_ACCEPT,
    DECISION_CHANGES_REQUESTED,
    DECISION_ESCALATE,
    STATUS_COMPLETED,
    FeedbackAnalysis,
    FeedbackRework,
    FeedbackVerdict,
    ReportKind,
)
from shikiru.domain.tasks import Role
from shikiru.engine.integration import (
    PUBLISHED_BASE_KEY,
    PUBLISHED_TIP_KEY,
    IntegrationBlocked,
    IntegrationHeld,
    PullRequestClosed,
)
from shikiru.errors import InvariantError, ShikiruError
from shikiru.paths import task_root
from shikiru.prompts.renderer import PromptName
from shikiru.workspace.github import PullRequestState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.config.settings import OrchestrationConfig
    from shikiru.domain.tasks import Task
    from shikiru.engine.step_runner import StepRunner
    from shikiru.store.events import EventLog
    from shikiru.store.feedback import FeedbackStore
    from shikiru.store.tasks import TaskStore
    from shikiru.workspace.git import Git
    from shikiru.workspace.github import GitHub, PullRequestComment, ReviewThread

type PreparePublication = Callable[[Task], Awaitable[Task]]

_SOURCE_LINK = "{id}: {url}"
_SOURCE_LINK_SEPARATOR = "; "
_QUESTION_SEPARATOR = "\n"
_EXHAUSTED = "feedback on {url} is not resolved after {attempts} implementation attempts ({links})"
_REJECTED = "the feedback changes on {url} were rejected: {issues} ({links})"
_ISSUE_SEPARATOR = "; "
_IMPLEMENTER_STOPPED = "the feedback rework on {url} stopped: {question} ({links})"
_NEEDS_OPERATOR = "feedback on {url} needs the Operator: {questions} ({links})"
_QUESTION_ITEM = "{id}: {question}"
_ESCALATED_BY_LEADER = "the Leader escalated feedback on {url}: {question} ({links})"
_DIVERGED = (
    "the remote branch {branch} of {url} holds commits the task branch does not "
    "(remote {remote_sha}, task {local_sha}); both sides are kept"
)
_REMOTE_MOVED = (
    "the remote branch {branch} of {url} moved to {remote_sha} while the round expected "
    "{expected}; both sides are kept"
)
_PUSH_FAILED = "publishing {branch} for {url} failed: {error}"
_REPLY_FAILED = "replying to {source} on {url} failed: {error}"
_ALIGN_FAILED = "reading the remote branch {branch} of {url} failed: {error}"
_THREAD_MISSING = "the discussion {source} is no longer on {url}; its reply cannot be posted"
_NOTHING_VERIFIED = "{task} round {round} has no verified tip to publish"
_ROUND_VANISHED = "{task} lost its feedback round while it ran"
_NO_ANALYSIS = "{task} round {round} reached {stage} without an analysis"
_REMOTE_REF = "+refs/heads/{branch}:refs/remotes/{remote}/{branch}"


class FeedbackBlocked(IntegrationBlocked):
    """A feedback round cannot go on without the Operator."""


class FeedbackSuperseded(Exception):
    """The pull request was merged, so its feedback needs no more work."""


class FeedbackRounds:
    """Run the persisted feedback round of a task's pull request.

    The Leader analyses and verifies, the Implementer makes the accepted changes, and this
    class publishes the verified commits and sends the verified replies itself.
    """

    def __init__(
        self,
        *,
        store: FeedbackStore,
        steps: StepRunner,
        github: GitHub,
        git: Git,
        tasks: TaskStore,
        events: EventLog,
        project_root: Path,
        remote: str,
        settings: OrchestrationConfig,
    ) -> None:
        """Run turns through `steps`, publish to `remote` of `project_root`, and reply via `github`.

        `settings.max_iterations` bounds the implementation attempts of one round.
        """
        self._store = store
        self._steps = steps
        self._github = github
        self._git = git
        self._tasks = tasks
        self._events = events
        self._project_root = project_root
        self._remote = remote
        self._settings = settings

    def pending(self, task_id: str) -> bool:
        """Return whether `task_id` has an unfinished feedback round."""
        return self._store.active(task_id) is not None

    async def run(self, task: Task, prepare: PreparePublication) -> None:
        """Carry `task`'s unfinished round as far as it goes.

        A round that waited for the Operator resumes where it stopped with a fresh attempt
        budget. `prepare` rebases the branch before publication and returns the current task.
        Raises `FeedbackBlocked` when the Operator must decide, `PullRequestClosed` when the
        pull request closed unmerged, and `IntegrationHeld` when the task is held.
        """
        active = self._store.active(task.id)
        if active is None:
            return
        if active.blocked:
            self._store.reset_after_retry(active.id, active.stage)
        try:
            await self._advance(task, prepare)
        except FeedbackSuperseded:
            return

    async def _advance(self, task: Task, prepare: PreparePublication) -> None:
        current = task
        while (active := self._store.active(task.id)) is not None:
            self._require_not_held(task.id)
            await self._require_open(active)
            match active.stage:
                case FeedbackStage.ANALYSIS:
                    current = await self._analyse(current, active)
                case FeedbackStage.IMPLEMENTATION:
                    current = await self._implement(current, active)
                case FeedbackStage.VERIFICATION:
                    current = await self._verify(current, active)
                case FeedbackStage.PUBLICATION:
                    current = await self._publish(current, active, prepare)
                case FeedbackStage.REPLIES:
                    await self._reply(active)
                case _:
                    message = f"{task.id} round {active.id} is unfinished at {active.stage}"
                    raise InvariantError(message)

    async def _analyse(self, task: Task, active: FeedbackRound) -> Task:
        current, outcome = await self._steps.run_report_turn(
            task,
            role=Role.LEADER,
            template=PromptName.FEEDBACK_ANALYSIS,
            kind=ReportKind.FEEDBACK_ANALYSIS,
            feedback=FeedbackInput(snapshot=active.snapshot),
        )
        if not isinstance(outcome, FeedbackAnalysis):
            message = f"{task.id} feedback analysis produced no analysis"
            raise InvariantError(message)
        match next_feedback_step(outcome):
            case FeedbackStep.ESCALATE:
                self._store.save_analysis(active.id, outcome, FeedbackStage.ANALYSIS)
                questions = _QUESTION_SEPARATOR.join(
                    _QUESTION_ITEM.format(id=source_id, question=question)
                    for source_id, question in operator_questions(outcome)
                )
                raise self._blocked(
                    active,
                    _NEEDS_OPERATOR.format(
                        url=active.pull_request_url,
                        questions=questions,
                        links=self._source_links(active),
                    ),
                )
            case FeedbackStep.IMPLEMENT:
                await self._align_with_remote(current, active)
                self._store.save_analysis(active.id, outcome, FeedbackStage.IMPLEMENTATION)
            case FeedbackStep.VERIFY:
                self._store.save_analysis(active.id, outcome, FeedbackStage.VERIFICATION)
        return current

    async def _implement(self, task: Task, active: FeedbackRound) -> Task:
        analysis = self._analysis_of(active)
        limit = self._settings.max_iterations
        if active.attempts >= limit:
            raise self._blocked(
                active,
                _EXHAUSTED.format(
                    url=active.pull_request_url,
                    attempts=active.attempts,
                    links=self._source_links(active),
                ),
            )
        current, outcome = await self._steps.run_report_turn(
            task,
            role=Role.IMPLEMENTER,
            template=PromptName.FEEDBACK_REWORK,
            kind=ReportKind.FEEDBACK_REWORK,
            feedback=FeedbackInput(snapshot=active.snapshot, analysis=analysis),
        )
        if not isinstance(outcome, FeedbackRework):
            message = f"{task.id} feedback rework produced no rework report"
            raise InvariantError(message)
        if outcome.status != STATUS_COMPLETED:
            raise self._blocked(
                active,
                _IMPLEMENTER_STOPPED.format(
                    url=active.pull_request_url,
                    question=outcome.question,
                    links=self._source_links(active),
                ),
            )
        self._store.complete_implementation(
            active.id, active.attempts + 1, describe_checks(outcome.tests)
        )
        return current

    async def _verify(self, task: Task, active: FeedbackRound) -> Task:
        analysis = self._analysis_of(active)
        final_round = active.attempts >= self._settings.max_iterations
        tip = await self._git.rev_parse(task_root(self._project_root, task.id), self._branch(task))
        current, outcome = await self._steps.run_report_turn(
            task,
            role=Role.LEADER,
            template=PromptName.FEEDBACK_VERIFY,
            kind=ReportKind.FEEDBACK_VERDICT,
            feedback=FeedbackInput(
                snapshot=active.snapshot, analysis=analysis, final_round=final_round, tip=tip
            ),
        )
        if not isinstance(outcome, FeedbackVerdict):
            message = f"{task.id} feedback verification produced no verdict"
            raise InvariantError(message)
        links = self._source_links(active)
        if outcome.decision == DECISION_ACCEPT:
            replies = publishable_replies(active.snapshot, analysis, outcome)
            if analysis.work:
                self._store.save_verified(
                    active.id, sha=tip, replies=replies, stage=FeedbackStage.PUBLICATION
                )
            else:
                self._store.save_verified(
                    active.id,
                    sha=tip,
                    replies=replies,
                    stage=FeedbackStage.REPLIES,
                    planned=self._planned_replies(active, replies, None),
                )
        elif outcome.decision == DECISION_CHANGES_REQUESTED and analysis.work:
            self._store.set_stage(active.id, FeedbackStage.IMPLEMENTATION)
        elif outcome.decision == DECISION_ESCALATE and outcome.escalation is not None:
            raise self._blocked(
                active,
                _ESCALATED_BY_LEADER.format(
                    url=active.pull_request_url, question=outcome.escalation.question, links=links
                ),
            )
        else:
            issues = _ISSUE_SEPARATOR.join(issue.description for issue in outcome.issues)
            raise self._blocked(
                active,
                _REJECTED.format(
                    url=active.pull_request_url, issues=issues or outcome.summary, links=links
                ),
            )
        return current

    async def _publish(
        self, task: Task, active: FeedbackRound, prepare: PreparePublication
    ) -> Task:
        verified = active.verified_sha
        if verified is None:
            message = _NOTHING_VERIFIED.format(task=task.id, round=active.id)
            raise InvariantError(message)
        task_dir = task_root(self._project_root, task.id)
        branch = self._branch(task)
        remote_head = await self._observed_head(active)
        local_tip = await self._git.rev_parse(task_dir, branch)
        if remote_head == verified and local_tip == verified:
            await self._published(task, active, verified)
            return task
        current = await prepare(task)
        tip = await self._git.rev_parse(task_dir, branch)
        if tip != verified:
            self._store.set_stage(active.id, FeedbackStage.VERIFICATION)
            return current
        self._require_not_held(task.id)
        remote_head = await self._observed_head(active)
        match plan_publication(
            expected=active.expected_remote_sha, remote_head=remote_head, local_tip=tip
        ):
            case PublicationAction.REMOTE_MOVED:
                raise self._blocked(
                    active,
                    _REMOTE_MOVED.format(
                        branch=branch,
                        url=active.pull_request_url,
                        remote_sha=remote_head,
                        expected=active.expected_remote_sha,
                    ),
                )
            case PublicationAction.PUSH:
                await self._prepared(task, active, tip)
                await self._push(active, branch, active.expected_remote_sha)
            case PublicationAction.ALREADY_PUBLISHED:
                pass
        await self._published(task, active, tip)
        return current

    async def _push(self, active: FeedbackRound, branch: str, lease: str) -> None:
        try:
            await self._git.push(self._project_root, self._remote, branch, lease=lease)
        except ShikiruError as error:
            raise self._blocked(
                active,
                _PUSH_FAILED.format(branch=branch, url=active.pull_request_url, error=error),
            ) from error

    async def _prepared(self, task: Task, active: FeedbackRound, sha: str) -> None:
        base_sha = await self._git.merge_base(
            task_root(self._project_root, task.id), self._base_ref(task), sha
        )
        self._events.append(
            EventKind.FEEDBACK_PREPARED,
            task_id=active.task_id,
            message=f"{active.task_id} prepared {sha[:SHORT_SHA_LENGTH]} for the feedback",
            data={
                "url": active.pull_request_url,
                "round": active.id,
                PUBLISHED_TIP_KEY: sha,
                PUBLISHED_BASE_KEY: base_sha,
            },
        )

    async def _published(self, task: Task, active: FeedbackRound, sha: str) -> None:
        base_sha = await self._git.merge_base(
            task_root(self._project_root, task.id), self._base_ref(task), sha
        )
        self._store.save_published(active.id, sha)
        self._events.append(
            EventKind.FEEDBACK_PUBLISHED,
            task_id=active.task_id,
            message=f"{active.task_id} published {sha[:SHORT_SHA_LENGTH]} for the feedback",
            data={
                "url": active.pull_request_url,
                "round": active.id,
                PUBLISHED_TIP_KEY: sha,
                PUBLISHED_BASE_KEY: base_sha,
            },
        )
        self._store.plan_replies(
            active.id, self._planned_replies(active, active.verified_replies, sha)
        )

    def _planned_replies(
        self, active: FeedbackRound, replies: dict[str, str], fixed_sha: str | None
    ) -> list[FeedbackReply]:
        analysis = self._analysis_of(active)
        fixed: set[str] = fixed_source_ids(analysis) if fixed_sha is not None else set()
        planned: list[FeedbackReply] = []
        for source in active.snapshot.sources:
            reply = replies.get(source.id)
            if reply is None:
                continue
            marker = reply_marker(active.task_id, active.id, source.id)
            body = reply_body(
                reply=reply,
                marker=marker,
                review_url=source.url if source.kind is FeedbackSourceKind.REVIEW_BODY else None,
                fixed_sha=fixed_sha if source.id in fixed else None,
                checks=active.checks,
            )
            planned.append(
                FeedbackReply(
                    round_id=active.id,
                    source_id=source.id,
                    kind=source.kind,
                    marker=marker,
                    body=body,
                )
            )
        return planned

    async def _reply(self, active: FeedbackRound) -> None:
        for reply in self._store.replies(active.id):
            if reply.state is ReplyState.SENT:
                continue
            self._require_not_held(active.task_id)
            await self._require_open(active)
            await self._send(active, reply)
        self._store.set_stage(active.id, FeedbackStage.HANDLED)
        self._events.append(
            EventKind.FEEDBACK_HANDLED,
            task_id=active.task_id,
            message=f"{active.task_id} answered the feedback on {active.pull_request_url}",
            data={"url": active.pull_request_url, "round": active.id},
        )

    async def _send(self, active: FeedbackRound, reply: FeedbackReply) -> None:
        url = active.pull_request_url
        try:
            thread = await self._thread_of(active, reply)
            if reply.state is ReplyState.SENDING:
                found = await self._find(url, reply, thread)
                if found is not None:
                    self._store.set_reply_state(
                        active.id, reply.source_id, ReplyState.SENT, found.url
                    )
                    return
            self._require_not_held(active.task_id)
            await self._require_open(active)
            self._store.set_reply_state(active.id, reply.source_id, ReplyState.SENDING)
            posted = await self._post(url, reply, thread)
        except PullRequestClosed, FeedbackBlocked:
            raise
        except ShikiruError as error:
            raise self._blocked(
                active,
                _REPLY_FAILED.format(source=reply.source_id, url=url, error=error),
            ) from error
        self._store.set_reply_state(active.id, reply.source_id, ReplyState.SENT, posted.url)

    async def _thread_of(self, active: FeedbackRound, reply: FeedbackReply) -> ReviewThread | None:
        if reply.kind is not FeedbackSourceKind.DISCUSSION:
            return None
        feedback = await self._github.pull_request_feedback(
            self._project_root, active.pull_request_url
        )
        self._end_unless_open(active, feedback.state)
        for thread in feedback.threads:
            if thread.id == reply.source_id:
                return thread
        raise self._blocked(
            active,
            _THREAD_MISSING.format(source=reply.source_id, url=active.pull_request_url),
        )

    async def _find(
        self, url: str, reply: FeedbackReply, thread: ReviewThread | None
    ) -> PullRequestComment | None:
        if thread is not None:
            return await self._github.find_thread_reply(
                self._project_root, url, thread=thread, marker=reply.marker
            )
        return await self._github.find_conversation_comment(
            self._project_root, url, marker=reply.marker
        )

    async def _post(
        self, url: str, reply: FeedbackReply, thread: ReviewThread | None
    ) -> PullRequestComment:
        if thread is not None:
            return await self._github.reply_to_thread(
                self._project_root, url, thread=thread, body=reply.body
            )
        return await self._github.post_conversation_comment(
            self._project_root, url, body=reply.body
        )

    async def _align_with_remote(self, task: Task, active: FeedbackRound) -> None:
        task_dir = task_root(self._project_root, task.id)
        branch = self._branch(task)
        expected = active.expected_remote_sha
        try:
            await self._git.fetch(
                self._project_root,
                self._remote,
                _REMOTE_REF.format(branch=branch, remote=self._remote),
                timeout=self._settings.network.fetch_timeout_seconds,
            )
            alignment = align_with_remote(
                remote_in_local=await self._git.merge_base_is_ancestor(task_dir, expected, branch),
                local_in_remote=await self._git.merge_base_is_ancestor(task_dir, branch, expected),
            )
            if alignment is Alignment.FAST_FORWARD:
                await self._git.merge_ff_only(task_dir, expected)
        except ShikiruError as error:
            raise self._blocked(
                active,
                _ALIGN_FAILED.format(branch=branch, url=active.pull_request_url, error=error),
            ) from error
        if alignment is Alignment.DIVERGED:
            raise self._blocked(
                active,
                _DIVERGED.format(
                    branch=branch,
                    url=active.pull_request_url,
                    remote_sha=expected,
                    local_sha=await self._git.rev_parse(task_dir, branch),
                ),
            )

    async def _observed_head(self, active: FeedbackRound) -> str:
        feedback = await self._github.pull_request_feedback(
            self._project_root, active.pull_request_url
        )
        self._end_unless_open(active, feedback.state)
        return feedback.head_sha

    async def _require_open(self, active: FeedbackRound) -> None:
        status = await self._github.pull_request_status(self._project_root, active.pull_request_url)
        self._end_unless_open(active, status.state)

    def _end_unless_open(self, active: FeedbackRound, state: PullRequestState) -> None:
        if state is PullRequestState.OPEN:
            return
        self._store.set_stage(active.id, FeedbackStage.ABANDONED)
        if state is PullRequestState.CLOSED:
            raise PullRequestClosed(active.pull_request_url)
        raise FeedbackSuperseded

    def _require_not_held(self, task_id: str) -> None:
        current = self._tasks.get(task_id)
        if current is None:
            message = _ROUND_VANISHED.format(task=task_id)
            raise InvariantError(message)
        if current.held:
            raise IntegrationHeld

    def _blocked(self, active: FeedbackRound, question: str) -> FeedbackBlocked:
        self._store.set_blocked(active.id, blocked=True)
        return FeedbackBlocked(question)

    def _analysis_of(self, active: FeedbackRound) -> FeedbackAnalysis:
        if active.analysis is None:
            message = _NO_ANALYSIS.format(task=active.task_id, round=active.id, stage=active.stage)
            raise InvariantError(message)
        return active.analysis

    def _source_links(self, active: FeedbackRound) -> str:
        return _SOURCE_LINK_SEPARATOR.join(
            _SOURCE_LINK.format(id=source.id, url=source.url) for source in active.snapshot.sources
        )

    def _base_ref(self, task: Task) -> str:
        if task.repo is None:
            message = f"{task.id} has no task branch"
            raise InvariantError(message)
        return task.repo.base_ref

    def _branch(self, task: Task) -> str:
        if task.repo is None:
            message = f"{task.id} has no task branch"
            raise InvariantError(message)
        return task.repo.branch
