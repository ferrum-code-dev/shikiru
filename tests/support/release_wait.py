import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from rich.cells import cell_len
from textual.widgets import Button

from shikiru.clock import SystemClock
from shikiru.process import SubprocessRunner
from tests.support.tmux import CONFIG_FLAG, SOCKET_FLAG
from tests.support.waiting import WaitTimeoutError, wait_until

RUNNER = SubprocessRunner()
CLOCK = SystemClock()
CONTROL_REARM_INTERVAL = timedelta(seconds=Button().active_effect_duration)
WAIT_TIMEOUT = 20
COMMAND_TIMEOUT = 5
INVALID_MODE_EXIT_CODE = 2
TASK_WAIT_TIMEOUT = 120
TASK_STATUS_DONE = "done"
TASK_FINISHED_STATUSES = frozenset({TASK_STATUS_DONE, "escalated", "cancelled"})
EXIT_BUTTON_LABEL = "Exit"
BACK_BUTTON_LABEL = "Back"
CANCEL_BUTTON_LABEL = "Cancel"
TMUX_CONFIG_NULL = "/dev/null"
ENTER_KEY = "Enter"
CLIENT_TTY_FORMAT = "#{client_tty}"
PANE_EXIT_FORMAT = "#{pane_dead} #{pane_dead_status}"
PANE_DEAD_VALUE = "1"
SUCCESS_EXIT_STATUS = "0"
PANE_EXIT_STATE_FIELD_COUNT = 2
TERMINAL_COORDINATE_OFFSET = 1
BUTTON_CENTER_DIVISOR = 2
MOUSE_LEFT_BUTTON = 0
MOUSE_PRESS_FINAL = "M"
MOUSE_RELEASE_FINAL = "m"
LADDER_FIELD_LABEL = "Ladder entries"
PANEL_VERTICAL_BORDER = "│"
INPUT_CLICK_PADDING = 2
PANE_HISTORY_ARGUMENTS = ("-S", "-")


def _tmux_command(tmux: str, socket: str, *arguments: str) -> list[str]:
    return [tmux, SOCKET_FLAG, socket, CONFIG_FLAG, TMUX_CONFIG_NULL, *arguments]


async def main() -> None:
    mode, *values = sys.argv[1:]
    handler = MODES.get(mode)
    if handler is None:
        raise SystemExit(INVALID_MODE_EXIT_CODE)
    await handler(*values)


async def wait_for_pane(
    tmux: str,
    socket: str,
    target: str,
    expected: str,
    timeout: float | str = WAIT_TIMEOUT,
) -> None:
    command = _tmux_command(tmux, socket, "capture-pane", "-p", "-t", target)
    capture = ""

    async def found() -> bool:
        nonlocal capture
        result = await RUNNER.run(command, timeout=COMMAND_TIMEOUT)
        capture = result.stdout
        return result.returncode == 0 and expected in capture

    try:
        await wait_until(found, timeout=float(timeout))
    except WaitTimeoutError:
        sys.stderr.write(capture + "\n")
        raise
    sys.stdout.write(capture + "\n")


async def _wait_for_control_rearm() -> None:
    ready_at = CLOCK.now() + CONTROL_REARM_INTERVAL
    await wait_until(lambda: CLOCK.now() >= ready_at, timeout=WAIT_TIMEOUT)


async def advance_page(tmux: str, socket: str, target: str, expected: str) -> None:
    await _wait_for_control_rearm()
    result = await RUNNER.run(
        _tmux_command(tmux, socket, "send-keys", "-t", target, ENTER_KEY),
        timeout=COMMAND_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    await wait_for_pane(tmux, socket, target, expected)


async def capture_pane(tmux: str, socket: str, target: str, *, history: bool = False) -> str:
    arguments = PANE_HISTORY_ARGUMENTS if history else ()
    command = _tmux_command(tmux, socket, "capture-pane", "-p", "-t", target, *arguments)
    result = await RUNNER.run(command, timeout=COMMAND_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


async def wait_for_task(tool: str, project: str, status_file: str) -> None:
    command = [tool, "--project", project, "--json", "status", "T-1"]
    path = Path(status_file)
    latest = ""

    async def complete() -> bool:
        nonlocal latest
        result = await RUNNER.run(command, timeout=COMMAND_TIMEOUT)
        if result.returncode != 0:
            return False
        latest = result.stdout
        await asyncio.to_thread(path.write_text, latest)
        value = json.loads(latest)
        task = value.get("task", {}).get("task", {})
        return task.get("status") in TASK_FINISHED_STATUSES

    try:
        await wait_until(complete, timeout=TASK_WAIT_TIMEOUT)
    except WaitTimeoutError:
        sys.stderr.write(latest + "\n")
        raise
    value = json.loads(latest)
    task = value.get("task", {}).get("task", {})
    if task.get("status") != TASK_STATUS_DONE:
        sys.stderr.write(latest + "\n")
        raise SystemExit(1)


async def wait_for_client(tmux: str, socket: str, target: str) -> None:
    command = _tmux_command(tmux, socket, "list-clients", "-t", target, "-F", CLIENT_TTY_FORMAT)
    client = ""

    async def connected() -> bool:
        nonlocal client
        result = await RUNNER.run(command, timeout=COMMAND_TIMEOUT)
        client = result.stdout.splitlines()[0] if result.returncode == 0 and result.stdout else ""
        return bool(client)

    await wait_until(connected, timeout=WAIT_TIMEOUT)
    sys.stdout.write(client + "\n")


async def wait_for_exit(
    tmux: str, socket: str, target: str, expected: str, status: str = SUCCESS_EXIT_STATUS
) -> None:
    capture = ""

    async def exited() -> bool:
        nonlocal capture
        result = await RUNNER.run(
            _tmux_command(tmux, socket, "display-message", "-p", "-t", target, PANE_EXIT_FORMAT),
            timeout=COMMAND_TIMEOUT,
        )
        if result.returncode != 0:
            return False
        state = result.stdout.split()
        if len(state) != PANE_EXIT_STATE_FIELD_COUNT or state[0] != PANE_DEAD_VALUE:
            return False
        capture = await capture_pane(tmux, socket, target, history=True)
        if state[1] != status:
            sys.stderr.write(capture + "\n")
            message = f"CLI exited with status {state[1]}"
            raise RuntimeError(message)
        if expected and expected not in capture:
            sys.stderr.write(capture + "\n")
            message = f"CLI output did not contain {expected!r}"
            raise RuntimeError(message)
        return True

    try:
        await wait_until(exited, timeout=WAIT_TIMEOUT)
    except WaitTimeoutError:
        sys.stderr.write(capture + "\n")
        raise
    sys.stdout.write(capture + "\n")


def _button_position(capture: str, label: str) -> tuple[int, int] | None:
    position = None
    for row, line in enumerate(capture.splitlines()):
        footer = label == EXIT_BUTTON_LABEL or (
            BACK_BUTTON_LABEL in line and CANCEL_BUTTON_LABEL in line
        )
        if label in line and footer:
            column = cell_len(line[: line.index(label)]) + cell_len(label) // BUTTON_CENTER_DIVISOR
            position = column + TERMINAL_COORDINATE_OFFSET, row + TERMINAL_COORDINATE_OFFSET
    return position


async def _click_at(tmux: str, socket: str, target: str, x: int, y: int) -> None:
    press = f"\x1b[<{MOUSE_LEFT_BUTTON};{x};{y}{MOUSE_PRESS_FINAL}"
    release = f"\x1b[<{MOUSE_LEFT_BUTTON};{x};{y}{MOUSE_RELEASE_FINAL}"
    for event in (press, release):
        result = await RUNNER.run(
            _tmux_command(tmux, socket, "send-keys", "-l", "-t", target, event),
            timeout=COMMAND_TIMEOUT,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)


async def click_button(tmux: str, socket: str, target: str, label: str, expected: str) -> None:
    await _wait_for_control_rearm()
    capture = await capture_pane(tmux, socket, target)
    if expected and expected in capture:
        return
    position = _button_position(capture, label)
    if position is None:
        message = f"navigation button is not visible: {label}"
        raise RuntimeError(message)
    await _click_at(tmux, socket, target, *position)
    if expected:
        await wait_for_pane(tmux, socket, target, expected)


async def set_ladder(tmux: str, socket: str, target: str, scope: str, value: str) -> None:
    capture = ""

    async def entered() -> bool:
        nonlocal capture
        capture = await capture_pane(tmux, socket, target)
        if f"Edit {scope}" not in capture:
            return False
        expected = f"{scope} {value}"
        if any(
            " ".join(segment.split()) == expected
            for line in capture.splitlines()
            for segment in line.split(PANEL_VERTICAL_BORDER)
        ):
            return True
        rows = capture.splitlines()
        row = next((index for index, line in enumerate(rows) if LADDER_FIELD_LABEL in line), None)
        if row is None:
            return False
        line = rows[row]
        outer_border = line.rfind(PANEL_VERTICAL_BORDER)
        inner_border = line.rfind(PANEL_VERTICAL_BORDER, 0, outer_border)
        if inner_border < 0:
            return False
        x = cell_len(line[:inner_border]) - INPUT_CLICK_PADDING
        y = row + TERMINAL_COORDINATE_OFFSET
        await _click_at(tmux, socket, target, x, y)
        for arguments in (
            ("send-keys", "-t", target, "C-a"),
            ("send-keys", "-t", target, "C-k"),
            ("send-keys", "-l", "-t", target, value),
        ):
            result = await RUNNER.run(
                _tmux_command(tmux, socket, *arguments), timeout=COMMAND_TIMEOUT
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
        return False

    try:
        await wait_until(entered, timeout=WAIT_TIMEOUT)
    except WaitTimeoutError:
        sys.stderr.write(capture + "\n")
        raise


MODES = {
    "pane": wait_for_pane,
    "advance": advance_page,
    "task": wait_for_task,
    "client": wait_for_client,
    "exit": wait_for_exit,
    "click": click_button,
    "ladder": set_ladder,
}


asyncio.run(main())
