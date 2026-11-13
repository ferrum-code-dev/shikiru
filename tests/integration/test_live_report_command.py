"""The live report command hands its input path to the shell exactly as written."""

from __future__ import annotations

from pathlib import Path

import pytest

from shikiru.operator.probe import REPORT_COMMAND, live_report_command
from shikiru.process import SubprocessRunner

_SHELL = "/bin/sh"
_PRINT_ARGUMENT = "printf '%s\\n' {argument}"
_SHELL_TIMEOUT_SECONDS = 10.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "report_input",
    [
        "/work/tree/contract-report-input.json",
        "/work/a b/contract-report-input.json",
        "/work/$HOME/contract-report-input.json",
        "/work/`id`/contract-report-input.json",
        "/work/\"quoted\"/'single'/contract-report-input.json",
        "/work/x;y&z|w/$(id)/contract-report-input.json",
    ],
    ids=("plain", "space", "dollar", "backtick", "quotes", "operators-and-substitution"),
)
async def test_report_command_passes_the_input_path_to_the_shell_intact(
    report_input: str,
) -> None:
    path = Path(report_input)
    argument = live_report_command(path)[len(REPORT_COMMAND) + 1 :]

    result = await SubprocessRunner().run(
        [_SHELL, "-c", _PRINT_ARGUMENT.format(argument=argument)],
        timeout=_SHELL_TIMEOUT_SECONDS,
        check=True,
    )

    assert result.stdout == f"{path}\n"
