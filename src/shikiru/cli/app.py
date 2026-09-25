"""The `shikiru` Typer application and its command adapters."""

from __future__ import annotations

import asyncio
import json
import os
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn

import typer
from pydantic import BaseModel
from rich.console import Console

from shikiru.agents.contracts import parse_rung
from shikiru.bootstrap import (
    build_doctor,
    build_engine,
    build_lifecycle,
    build_mcp_server,
    build_operator,
    build_project_detectors,
)
from shikiru.cli.context import CliState, project_root, state_from
from shikiru.cli.lifecycle import CLI_DASH_COMMAND, CLI_ENGINE_COMMAND, PACKAGE_NAME
from shikiru.cli.mcp_adapter import run_mcp
from shikiru.cli.render import ack_renderable, doctor_renderable, status_renderable
from shikiru.cli.report import report_command
from shikiru.config.loader import load_settings
from shikiru.config.settings import ComplexityName
from shikiru.domain.tasks import Mode
from shikiru.errors import ShikiruError, UsageError
from shikiru.operator.commands import Ack, AckStatus
from shikiru.operator.detectors import initialize_project

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from shikiru.config.settings import Settings
    from shikiru.operator.services import OperatorServices

PROJECT_OPTION = typer.Option("--project", help="Project directory.")
JSON_OPTION = typer.Option("--json", help="Print JSON output.")
OVERRIDES_OPTION = typer.Option("--set", help="Override a config key.")
ATTACH_OPTION = typer.Option("--attach", help="Attach to the tmux session after start.")
KILL_SESSION_OPTION = typer.Option("--kill-session", help="Kill the project tmux session.")
WATCH_OPTION = typer.Option("--watch")
PROBE_OPTION = typer.Option("--probe", help="Run live agent contract checks.")

app = typer.Typer(name=PACKAGE_NAME, no_args_is_help=True)
app.command(name="report", hidden=True)(report_command)


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
            help="Print the version and exit.",
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
        typer.Option("--yes", help="Write detected defaults without prompting."),
    ] = False,
) -> None:
    """Initialize a project from detected repositories, plugins and trackers."""
    if not yes:
        _fail(UsageError("interactive init is not available yet; use init --yes"))
    state = state_from(context)
    root = state.project.resolve() if state.project is not None else Path.cwd().resolve()
    try:
        config_path = _run(initialize_project(build_project_detectors(root)))
    except ShikiruError as error:
        _fail(error)
    _emit_value(state, {"config": str(config_path)}, f"initialized {config_path}")


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
    _emit_value(state, result, "engine started")


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
    emit_ack(state, result)


@app.command()
def submit(
    context: typer.Context,
    *,
    request: Annotated[str, typer.Argument(help="Task request.")],
    mode: Annotated[Mode | None, typer.Option("--mode")] = None,
    complexity: Annotated[ComplexityName, typer.Option("--complexity")] = "normal",
    priority: Annotated[int, typer.Option("--priority")] = 0,
    depends_on: Annotated[list[str] | None, typer.Option("--after")] = None,
    leader: Annotated[str | None, typer.Option("--leader")] = None,
    implementer: Annotated[str | None, typer.Option("--implementer")] = None,
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
    task_id: Annotated[str | None, typer.Argument()] = None,
    watch: Annotated[bool, WATCH_OPTION] = False,
) -> None:
    """Show engine, queue, task, escalation, usage and quota status."""
    try:
        state = state_from(context)
        root = project_root(state)
        settings = load_settings(root, state.overrides)
        services = build_operator(root, settings)
        try:
            _run(_status_loop(services, state, task_id, watch, settings=settings))
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
        no_color = bool(os.environ.get("NO_COLOR"))
        Console(no_color=no_color).print(doctor_renderable(report))
    if not report.ok:
        raise typer.Exit(code=1)


@app.command(name=CLI_ENGINE_COMMAND, hidden=True)
def engine(context: typer.Context) -> None:
    """Run the engine daemon in the current process."""
    try:
        state = state_from(context)
        root = project_root(state)
        daemon = build_engine(root, load_settings(root, state.overrides))
        _run(daemon.run())
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
        _run(build_lifecycle().dashboard(root, state.overrides, None if home else task))
    except ShikiruError as error:
        _fail(error)


@app.command(name="mcp")
def mcp(context: typer.Context) -> None:
    """Run the FastMCP stdio server for this project."""
    try:
        state = state_from(context)
        root = project_root(state)
        server, services = build_mcp_server(root, state.overrides)
        run_mcp(server, services)
    except ShikiruError as error:
        _fail(error)


async def _status_loop(
    services: OperatorServices,
    state: CliState,
    task_id: str | None,
    watch: bool,
    *,
    settings: Settings,
) -> None:
    def render() -> None:
        view = services.queries.status(task_id)
        if state.json_output:
            typer.echo(view.model_dump_json())
        else:
            no_color = bool(os.environ.get("NO_COLOR"))
            console = Console(no_color=no_color)
            console.print(status_renderable(view, settings=settings, width=console.width))

    render()
    if watch:

        async def on_change() -> None:
            render()

        await services.reader.watch(on_change)


def emit_ack(state: CliState, ack: Ack) -> None:
    """Render an acknowledgement and map rejected commands to exit code four."""
    if state.json_output:
        typer.echo(ack.model_dump_json())
    else:
        no_color = bool(os.environ.get("NO_COLOR"))
        Console(no_color=no_color).print(ack_renderable(ack))
    if ack.status is AckStatus.REJECTED:
        raise typer.Exit(code=4)


def _emit_value(state: CliState, value: object, human: str) -> None:
    if state.json_output:
        if isinstance(value, BaseModel):
            typer.echo(value.model_dump_json())
        else:
            typer.echo(json.dumps(value, default=str))
    elif human:
        typer.echo(human)


def _run[T](operation: Awaitable[T]) -> T:
    return asyncio.run(operation)


def _fail(error: ShikiruError) -> NoReturn:
    typer.echo(str(error), err=True)
    raise typer.Exit(code=error.exit_code)
