import asyncio
import json
import sys
from pathlib import Path

from shikiru.process import SubprocessRunner
from tests.support.tmux import CONFIG_FLAG, SOCKET_FLAG
from tests.support.waiting import WaitTimeoutError, wait_until

RUNNER = SubprocessRunner()
WAIT_TIMEOUT = 20
COMMAND_TIMEOUT = 5
INVALID_MODE_EXIT_CODE = 2
TASK_WAIT_TIMEOUT = 120
TASK_STATUS_DONE = "done"
TASK_FINISHED_STATUSES = frozenset({TASK_STATUS_DONE, "escalated", "cancelled"})
TMUX_CONFIG_NULL = "/dev/null"
ENTER_KEY = "Enter"
CLIENT_TTY_FORMAT = "#{client_tty}"


def _tmux_command(tmux: str, socket: str, *arguments: str) -> list[str]:
    return [tmux, SOCKET_FLAG, socket, CONFIG_FLAG, TMUX_CONFIG_NULL, *arguments]


async def main() -> None:
    mode, *values = sys.argv[1:]
    handler = MODES.get(mode)
    if handler is None:
        raise SystemExit(INVALID_MODE_EXIT_CODE)
    await handler(*values)


async def wait_for_pane(tmux: str, socket: str, target: str, expected: str) -> None:
    command = _tmux_command(tmux, socket, "capture-pane", "-p", "-t", target, "-S", "-")
    capture = ""

    async def found() -> bool:
        nonlocal capture
        result = await RUNNER.run(command, timeout=COMMAND_TIMEOUT)
        capture = result.stdout
        return result.returncode == 0 and expected in capture

    try:
        await wait_until(found, timeout=WAIT_TIMEOUT)
    except WaitTimeoutError:
        sys.stderr.write(capture + "\n")
        raise
    sys.stdout.write(capture + "\n")


async def advance_page(tmux: str, socket: str, target: str, expected: str) -> None:
    capture = ""

    async def advanced() -> bool:
        nonlocal capture
        capture = await capture_pane(tmux, socket, target)
        if expected in capture:
            return True
        result = await RUNNER.run(
            _tmux_command(tmux, socket, "send-keys", "-t", target, ENTER_KEY),
            timeout=COMMAND_TIMEOUT,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        return False

    try:
        await wait_until(advanced, timeout=WAIT_TIMEOUT)
    except WaitTimeoutError:
        sys.stderr.write(capture + "\n")
        raise
    sys.stdout.write(capture + "\n")


async def capture_pane(tmux: str, socket: str, target: str) -> str:
    command = _tmux_command(tmux, socket, "capture-pane", "-p", "-t", target, "-S", "-")
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


MODES = {
    "pane": wait_for_pane,
    "advance": advance_page,
    "task": wait_for_task,
    "client": wait_for_client,
}


asyncio.run(main())
