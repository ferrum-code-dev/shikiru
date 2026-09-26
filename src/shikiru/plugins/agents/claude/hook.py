"""Claude `PostToolUse` hook: stop the turn before a request would cross the limit (04 §4).

Run as `<python> -P -m shikiru.plugins.agents.claude.hook <state> <marker> <limit_tokens>`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from shikiru.plugins.agents.claude.context import (
    last_assistant_context,
    pending_tokens,
    read_transcript,
)

TRANSCRIPT_PATH_FIELD = "transcript_path"
TOOL_RESPONSE_FIELD = "tool_response"
STOP_REASON = "Shikiru: context threshold reached; compacting before the next request"
_CONTINUE_FIELD = "continue"
_STOP_REASON_FIELD = "stopReason"
_STATE_ID_FIELD = "id"
_STATE_TOKENS_FIELD = "tokens"


def main() -> None:
    """Record this tool result and stop the turn once the next request reaches the limit."""
    state_path, marker_path, limit = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    event = json.load(sys.stdin)
    message_id, context = last_assistant_context(
        read_transcript(Path(event[TRANSCRIPT_PATH_FIELD]))
    )
    pending = _record_pending(
        state_path, message_id, pending_tokens(event.get(TOOL_RESPONSE_FIELD))
    )
    if context + pending >= limit:
        marker_path.touch()
        json.dump({_CONTINUE_FIELD: False, _STOP_REASON_FIELD: STOP_REASON}, sys.stdout)


def _record_pending(state_path: Path, message_id: str | None, tokens: int) -> int:
    with state_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({_STATE_ID_FIELD: message_id, _STATE_TOKENS_FIELD: tokens}) + "\n")
    records = [json.loads(line) for line in state_path.read_text(encoding="utf-8").splitlines()]
    return sum(
        record[_STATE_TOKENS_FIELD] for record in records if record[_STATE_ID_FIELD] == message_id
    )


if __name__ == "__main__":
    main()
