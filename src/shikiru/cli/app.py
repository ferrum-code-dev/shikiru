"""The `shikiru` Typer application."""

from __future__ import annotations

from importlib.metadata import version

import typer

from shikiru.agents.sdk.hook_cli import hook_command
from shikiru.cli.report import report_command

PACKAGE_NAME = "shikiru"

app = typer.Typer(name=PACKAGE_NAME, no_args_is_help=True)
app.command(name="report", hidden=True)(report_command)
app.command(name="hook", hidden=True)(hook_command)


def _print_version(*, show: bool) -> None:
    if show:
        typer.echo(version(PACKAGE_NAME))
        raise typer.Exit


@app.callback()
def main(
    _version_flag: bool = typer.Option(
        False,
        "--version",
        callback=_print_version,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """Shikiru: run coding-agent tasks in tmux panes."""
