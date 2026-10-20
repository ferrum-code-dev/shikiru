"""The `shikiru` Typer application and its command adapters."""

from __future__ import annotations

import asyncio
import json
import os
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn

import typer
import typer.rich_utils as typer_rich_utils
from pydantic import BaseModel

from shikiru.agents.contracts import parse_rung
from shikiru.bootstrap import (
    build_doctor,
    build_engine_process,
    build_lifecycle,
    build_mcp_server,
    build_operator,
    build_project_detectors,
    build_session_tools,
    run_dashboard,
    run_init_wizard,
    run_palette,
)
from shikiru.cli.context import CliState, project_root, state_from
from shikiru.cli.render import (
    ack_renderable,
    doctor_renderable,
    error_renderable,
    init_renderable,
    session_rows_renderable,
    start_renderable,
    status_renderable,
)
from shikiru.cli.report import report_command
from shikiru.cli.view import view_command
from shikiru.config.loader import load_settings
from shikiru.config.settings import ComplexityName
from shikiru.domain.tasks import Mode
from shikiru.errors import (
    EXIT_COMMAND_REJECTED,
    EXIT_GENERIC_ERROR,
    ShikiruError,
    UsageError,
)
from shikiru.operator.commands import Ack, AckStatus
from shikiru.operator.lifecycle import (
    CLI_DASH_COMMAND,
    CLI_ENGINE_COMMAND,
    CLI_PALETTE_ORIGIN_PANE_OPTION,
    PACKAGE_NAME,
)
from shikiru.tmux.session import PROJECT_DIR_ENV_VAR
from shikiru.ui.console import human_console
from shikiru.ui.tokens import (
    ALERT_COLOR,
    NEUTRAL_BORDER_COLOR,
    PRIMARY_TEXT_COLOR,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STYLE_BOLD,
    STYLE_DIM,
)
from shikiru.view_log import CLI_VIEW_COMMAND

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from rich.console import RenderableType

    from shikiru.operator.services import OperatorServices

PROJECT_OPTION = typer.Option(
    "--project",
    help=("Select a project directory. [bold]Default:[/] the nearest initialized project."),
)
JSON_OPTION = typer.Option(
    "--json", help="Print machine-readable JSON. [bold]Default:[/] human-readable output."
)
OVERRIDES_OPTION = typer.Option(
    "--set",
    help=("Override config with key.path=value; repeat as needed. [bold]Default:[/] no overrides."),
)
ATTACH_OPTION = typer.Option(
    "--attach",
    help=(
        "Attach to the project session after starting or reusing the engine. "
        "[bold]Default:[/] stay detached."
    ),
)
KILL_SESSION_OPTION = typer.Option(
    "--kill-session",
    help=(
        "Close the project session even if the engine is stopped. "
        "[bold]Default:[/] keep the session."
    ),
)
WATCH_OPTION = typer.Option(
    "--watch", help="Refresh the report when project state changes. [bold]Default:[/] one report."
)
PROBE_OPTION = typer.Option(
    "--probe",
    help=(
        "Run live contract and smoke checks for configured role rungs. "
        "[bold]Default:[/] skip live agent probes."
    ),
)
REQUIRED_LEGEND = f"[bold {ALERT_COLOR}]*[/] marks a required argument."


def _configure_typer_help_palette() -> None:
    typer_rich_utils.STYLE_OPTION = f"{STYLE_BOLD} {STEEL_BLUE}"
    typer_rich_utils.STYLE_SWITCH = f"{STYLE_BOLD} {STEEL_BLUE}"
    typer_rich_utils.STYLE_NEGATIVE_OPTION = f"{STYLE_BOLD} {ALERT_COLOR}"
    typer_rich_utils.STYLE_NEGATIVE_SWITCH = f"{STYLE_BOLD} {ALERT_COLOR}"
    typer_rich_utils.STYLE_TYPES = SECONDARY_TEXT_COLOR
    typer_rich_utils.STYLE_TYPES_SEPARATOR = f"{STYLE_DIM} {SECONDARY_TEXT_COLOR}"
    typer_rich_utils.STYLE_USAGE = STEEL_BLUE
    typer_rich_utils.STYLE_USAGE_COMMAND = f"{STYLE_BOLD} {PRIMARY_TEXT_COLOR}"
    typer_rich_utils.STYLE_DEPRECATED = ALERT_COLOR
    typer_rich_utils.STYLE_DEPRECATED_COMMAND = f"{STYLE_DIM} {SECONDARY_TEXT_COLOR}"
    typer_rich_utils.STYLE_HELPTEXT_FIRST_LINE = PRIMARY_TEXT_COLOR
    typer_rich_utils.STYLE_HELPTEXT = f"{STYLE_DIM} {SECONDARY_TEXT_COLOR}"
    typer_rich_utils.STYLE_OPTION_HELP = PRIMARY_TEXT_COLOR
    typer_rich_utils.STYLE_OPTION_DEFAULT = f"{STYLE_DIM} {SECONDARY_TEXT_COLOR}"
    typer_rich_utils.STYLE_OPTION_ENVVAR = f"{STYLE_DIM} {SECONDARY_TEXT_COLOR}"
    typer_rich_utils.STYLE_REQUIRED_SHORT = ALERT_COLOR
    typer_rich_utils.STYLE_REQUIRED_LONG = f"{STYLE_DIM} {ALERT_COLOR}"
    typer_rich_utils.STYLE_OPTIONS_PANEL_BORDER = f"{STYLE_DIM} {NEUTRAL_BORDER_COLOR}"
    typer_rich_utils.STYLE_COMMANDS_PANEL_BORDER = f"{STYLE_DIM} {NEUTRAL_BORDER_COLOR}"
    typer_rich_utils.ARGUMENTS_PANEL_TITLE = f"[bold not dim {PRIMARY_TEXT_COLOR}]Arguments[/]"
    typer_rich_utils.OPTIONS_PANEL_TITLE = f"[bold not dim {PRIMARY_TEXT_COLOR}]Options[/]"
    typer_rich_utils.COMMANDS_PANEL_TITLE = f"[bold not dim {PRIMARY_TEXT_COLOR}]Commands[/]"
    typer_rich_utils.REQUIRED_LONG_STRING = ""
    typer_rich_utils.STYLE_COMMANDS_TABLE_FIRST_COLUMN = f"{STYLE_BOLD} {STEEL_BLUE}"
    typer_rich_utils.STYLE_ERRORS_PANEL_BORDER = ALERT_COLOR
    typer_rich_utils.STYLE_ERRORS_SUGGESTION = f"{STYLE_DIM} {SECONDARY_TEXT_COLOR}"
    typer_rich_utils.STYLE_ABORTED = ALERT_COLOR


_configure_typer_help_palette()

app = typer.Typer(name=PACKAGE_NAME, no_args_is_help=True)
app.command(name="report", hidden=True)(report_command)
app.command(name=CLI_VIEW_COMMAND, hidden=True)(view_command)


def _print_version(*, show: bool) -> None:
    if show:
        typer.echo(version(PACKAGE_NAME))
        raise typer.Exit


@app.callback()
def main(
    context: typer.Context,
    *,
    _version_flag: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = False,
    project: Annotated[Path | None, PROJECT_OPTION] = None,
    json_output: Annotated[bool, JSON_OPTION] = False,
    overrides: Annotated[list[str] | None, OVERRIDES_OPTION] = None,
) -> None:
    """Shikiru: run coding-agent tasks in tmux panes."""
    del _version_flag
    context.obj = CliState(project, json_output, tuple(overrides or ()))


@app.command()
def init(
    context: typer.Context,
    *,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help=(
                "Write detected defaults without opening the setup wizard. "
                "[bold]Default:[/] use the wizard."
            ),
        ),
    ] = False,
) -> None:
    """Initialize a project from detected repositories, plugins and trackers."""
    state = state_from(context)
    root = state.project.resolve() if state.project is not None else Path.cwd().resolve()
    try:
        if yes:
            config_path = _run(build_project_detectors(root).initialize())
        else:
            result = _run(run_init_wizard(root))
            if result is None:
                return
            if result.doctor_report is None or not result.doctor_report.ok:
                raise typer.Exit(code=EXIT_GENERIC_ERROR)
            config_path = result.config_path
    except ShikiruError as error:
        _fail(error)
    value = {"config": str(config_path)}
    if not state.json_output:
        console = human_console()
        console.print(
            init_renderable(
                config_path,
                console=console,
                include_placeholder_warning=yes,
            )
        )
    else:
        _emit_value(
            state,
            value,
            f"initialized {config_path}",
        )


@app.command()
def start(
    context: typer.Context,
    *,
    attach: Annotated[bool, ATTACH_OPTION] = False,
) -> None:
    """Validate config, start the detached engine and ensure its tmux session."""
    try:
        state = state_from(context)
        root = project_root(state)
        result = _run(build_lifecycle().start(root, state.overrides, attach=attach))
    except ShikiruError as error:
        _fail(error)
    _emit_value(state, result, start_renderable(result))


@app.command()
def stop(
    context: typer.Context,
    *,
    kill_session: Annotated[bool, KILL_SESSION_OPTION] = False,
) -> None:
    """Queue an orderly engine stop, optionally closing its tmux session."""
    try:
        state = state_from(context)
        root = project_root(state)
        result = _run(build_lifecycle().stop(root, state.overrides, kill_session=kill_session))
    except ShikiruError as error:
        _fail(error)
    emit_ack(state, result, stopping=True)


@app.command(name="ls")
def list_sessions(
    context: typer.Context,
    *,
    host: Annotated[
        str | None,
        typer.Option(
            "--host", help="List sessions on this SSH host instead. [bold]Default:[/] this host."
        ),
    ] = None,
    json_output: Annotated[bool, JSON_OPTION] = False,
) -> None:
    """List Shikiru project sessions visible on this host or an SSH host."""
    try:
        state = state_from(context)
        lister, _ = build_session_tools(state.overrides)
        rows = _run(lister.list_sessions(host))
    except ShikiruError as error:
        _fail(error)
    if state.json_output or json_output:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows]))
    else:
        console = human_console()
        console.print(session_rows_renderable(rows, host=host, console=console))


@app.command()
def attach(
    context: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Project or full session name to attach to; omit to choose a session."),
    ] = None,
    *,
    host: Annotated[
        str | None,
        typer.Option(
            "--host", help="Attach over SSH to this host instead. [bold]Default:[/] this host."
        ),
    ] = None,
) -> None:
    """Attach to a listed Shikiru session, with a picker when selection is needed."""
    try:
        state = state_from(context)
        lister, attacher = build_session_tools(state.overrides)

        async def attach_session() -> None:
            rows = await lister.list_sessions(host)
            await attacher.attach(
                rows,
                name=name,
                host=host,
                inside_tmux=bool(os.environ.get("TMUX")),
            )

        _run(attach_session())
    except ShikiruError as error:
        _fail(error)


@app.command(epilog=REQUIRED_LEGEND)
def submit(
    context: typer.Context,
    *,
    request: Annotated[str, typer.Argument(help="Task for triage and implementation.")],
    mode: Annotated[
        Mode | None,
        typer.Option(
            "--mode",
            help="Choose a task mode. [bold]Default:[/] the Leader decides during triage.",
        ),
    ] = None,
    complexity: Annotated[
        ComplexityName | None,
        typer.Option(
            "--complexity",
            help="Set task complexity. [bold]Default:[/] triage decides.",
        ),
    ] = None,
    priority: Annotated[
        int,
        typer.Option(
            "--priority",
            help="Higher values dispatch sooner. [bold]Default:[/] no priority (0).",
            show_default=False,
        ),
    ] = 0,
    depends_on: Annotated[
        list[str] | None,
        typer.Option(
            "--after",
            metavar="TASK_ID",
            help="Require earlier tasks first; repeat. [bold]Default:[/] no dependencies.",
        ),
    ] = None,
    leader: Annotated[
        str | None,
        typer.Option(
            "--leader",
            metavar="RUNG",
            help="Pin a rung using agent:model\\[:effort]. [bold]Default:[/] the project ladder.",
        ),
    ] = None,
    implementer: Annotated[
        str | None,
        typer.Option(
            "--implementer",
            metavar="RUNG",
            help="Pin a rung using agent:model\\[:effort]. [bold]Default:[/] the project ladder.",
        ),
    ] = None,
) -> None:
    """Queue one task and print its allocated `T-n` id."""
    try:
        state = state_from(context)
        root = project_root(state)
        settings = load_settings(root, state.overrides)
        services = build_operator(root, settings)
        try:
            ack = _run(
                services.commands.submit_task(
                    request,
                    mode=mode,
                    complexity=complexity,
                    priority=priority,
                    depends_on=depends_on or (),
                    leader=parse_rung(leader) if leader is not None else None,
                    implementer=parse_rung(implementer) if implementer is not None else None,
                )
            )
        finally:
            services.close()
    except ShikiruError as error:
        _fail(error)
    task_id = ack.result.get("task_id") if ack.result else None
    if task_id is None:
        emit_ack(state, ack)
    elif state.json_output:
        _emit_value(state, {"task_id": task_id, "command_id": ack.id, "status": ack.status}, "")
    else:
        typer.echo(task_id)


@app.command()
def status(
    context: typer.Context,
    *,
    task_id: Annotated[
        str | None, typer.Argument(help="Task ID to show; omit for project status.")
    ] = None,
    watch: Annotated[bool, WATCH_OPTION] = False,
) -> None:
    """Show engine, queue, task, escalation, usage and quota status."""
    try:
        state = state_from(context)
        root = project_root(state)
        settings = load_settings(root, state.overrides)
        services = build_operator(root, settings)
        try:
            _run(
                _status_loop(
                    services,
                    state,
                    task_id,
                    watch,
                    quota_warn_pct=settings.dashboard.gauges.quota_warn_pct,
                    quota_alert_pct=settings.dashboard.gauges.quota_alert_pct,
                )
            )
        finally:
            services.close()
    except ShikiruError as error:
        _fail(error)


@app.command()
def doctor(
    context: typer.Context,
    *,
    probe: Annotated[bool, PROBE_OPTION] = False,
) -> None:
    """Check the environment, project configuration and configured agent plugins."""
    try:
        state = state_from(context)
        root = project_root(state)
        report = _run(build_doctor(root).run(state.overrides, probe=probe))
    except ShikiruError as error:
        _fail(error)
    if state.json_output:
        typer.echo(report.model_dump_json())
    else:
        console = human_console()
        console.print(doctor_renderable(report, width=console.size.width))
    if not report.ok:
        raise typer.Exit(code=EXIT_GENERIC_ERROR)


@app.command(name=CLI_ENGINE_COMMAND, hidden=True)
def engine(context: typer.Context) -> None:
    """Run the engine daemon in the current process."""
    try:
        state = state_from(context)
        root = project_root(state)
        process = build_engine_process(root, load_settings(root, state.overrides))
        _run(process.run())
    except ShikiruError as error:
        _fail(error)


@app.command(name=CLI_DASH_COMMAND, hidden=True)
def dash(
    context: typer.Context,
    *,
    home: Annotated[bool, typer.Option("--home")] = False,
    task: Annotated[str | None, typer.Option("--task")] = None,
) -> None:
    """Run the Textual dashboard process in a tmux pane."""
    try:
        state = state_from(context)
        root = project_root(state)
        if home == (task is not None):
            message = "dash requires exactly one of --home or --task"
            raise UsageError(message)
        _run(run_dashboard(root, state.overrides, None if home else task))
    except ShikiruError as error:
        _fail(error)


@app.command(hidden=True)
def palette(
    context: typer.Context,
    *,
    origin_pane: Annotated[
        str | None,
        typer.Option(CLI_PALETTE_ORIGIN_PANE_OPTION, hidden=True),
    ] = None,
) -> None:
    """Run the operator palette for the invoking Shikiru tmux session."""
    try:
        state = state_from(context)
        project = state.project
        if project is None:
            session_project = os.environ.get(PROJECT_DIR_ENV_VAR)
            project = Path(session_project) if session_project is not None else None
        root = project_root(CliState(project, state.json_output, state.overrides))
        _run(run_palette(root, state.overrides, origin_pane))
    except ShikiruError as error:
        _fail(error)


@app.command(name="mcp")
def mcp(context: typer.Context) -> None:
    """Run the FastMCP stdio server for this project."""
    try:
        state = state_from(context)
        root = project_root(state)
        server, services = build_mcp_server(root, state.overrides)
        try:
            server.run(transport="stdio")
        finally:
            services.close()
    except ShikiruError as error:
        _fail(error)


async def _status_loop(
    services: OperatorServices,
    state: CliState,
    task_id: str | None,
    watch: bool,
    *,
    quota_warn_pct: int,
    quota_alert_pct: int,
) -> None:
    def render() -> None:
        if state.json_output:
            view = services.queries.status(task_id)
            typer.echo(view.model_dump_json())
        else:
            view = services.queries.human_status(task_id)
            console = human_console()
            uptime_seconds = (
                services.queries.current_engine_uptime_seconds()
                if view.status.task is None
                else None
            )
            console.print(
                status_renderable(
                    view,
                    width=console.width,
                    uptime_seconds=uptime_seconds,
                    quota_warn_pct=quota_warn_pct,
                    quota_alert_pct=quota_alert_pct,
                )
            )

    render()
    if watch:

        async def on_change() -> None:
            render()

        await services.reader.watch(on_change)


def emit_ack(
    state: CliState,
    ack: Ack,
    *,
    stopping: bool = False,
) -> None:
    """Render an acknowledgement and map rejected commands to their exit code."""
    if state.json_output:
        typer.echo(ack.model_dump_json())
    else:
        human_console().print(ack_renderable(ack, stopping=stopping))
    if ack.status is AckStatus.REJECTED:
        raise typer.Exit(code=EXIT_COMMAND_REJECTED)


def _emit_value(state: CliState, value: object, human: str | RenderableType) -> None:
    if state.json_output:
        if isinstance(value, BaseModel):
            typer.echo(value.model_dump_json())
        else:
            typer.echo(json.dumps(value, default=str))
    elif human:
        human_console().print(human)


def _run[T](operation: Awaitable[T]) -> T:
    return asyncio.run(operation)


def _fail(error: ShikiruError) -> NoReturn:
    human_console(stderr=True).print(error_renderable(error))
    raise typer.Exit(code=error.exit_code)
