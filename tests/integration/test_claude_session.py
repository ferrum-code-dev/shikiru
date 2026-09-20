from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from shutil import copyfile

from shikiru.agents.contracts import FailureKind, TurnFailed
from shikiru.agents.sdk.hooks import HOOKS_FILENAME
from shikiru.agents.sdk.prices import PriceTable
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.plugins.agents.claude.session import ClaudeSession
from tests.support.clock import FakeClock

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "agents" / "claude"
_PRICES_PATH = (
    Path(__file__).parents[2] / "src" / "shikiru" / "plugins" / "agents" / "claude" / "prices.toml"
)
_ROLE = "implementer"
_SESSION_ID = "33333333-3333-3333-3333-333333333333"
_MODEL = "claude-sonnet-4-5-20250929"
_START = datetime(2026, 1, 1, tzinfo=UTC)


async def test_claude_session_refreshes_quota_only_for_quota_failures(tmp_path: Path) -> None:
    cases = (
        ("quota", FailureKind.QUOTA, 1),
        ("other", FailureKind.OTHER, 0),
    )
    for name, expected_kind, expected_invalidations in cases:
        run_dir = tmp_path / name
        role_dir = run_dir / _ROLE
        role_dir.mkdir(parents=True)
        copyfile(_FIXTURES_DIR / f"hooks_{name}_error.jsonl", role_dir / HOOKS_FILENAME)
        copyfile(
            _FIXTURES_DIR / f"transcript_{name}_error.jsonl",
            role_dir / f"transcript_{name}_error.jsonl",
        )
        invalidations: list[None] = []
        clock = FakeClock(_START)
        session = ClaudeSession(
            run_dir,
            _ROLE,
            _SESSION_ID,
            0,
            clock=clock,
            reset_time=ResetTimeParser(clock),
            prices=PriceTable.from_toml(_PRICES_PATH),
            model=_MODEL,
            api_key_auth=False,
            invalidate_quota=lambda invalidations=invalidations: invalidations.append(None),
        )

        signals = session.signals()
        signal = await signals.__anext__()
        await signals.aclose()

        assert isinstance(signal, TurnFailed)
        assert signal.kind is expected_kind
        assert len(invalidations) == expected_invalidations


def test_absolute_transcript_path_is_not_rebased(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    role_dir = run_dir / _ROLE
    role_dir.mkdir(parents=True)
    absolute_path = tmp_path / "outside" / "transcript.jsonl"
    (role_dir / absolute_path.name).write_text("{}\n", encoding="utf-8")
    clock = FakeClock(_START)
    session = ClaudeSession(
        run_dir,
        _ROLE,
        _SESSION_ID,
        0,
        clock=clock,
        reset_time=ResetTimeParser(clock),
        prices=PriceTable.from_toml(_PRICES_PATH),
        model=_MODEL,
        api_key_auth=False,
        invalidate_quota=lambda: None,
        transcript_path=str(absolute_path),
    )

    assert session.snapshot()["transcript_path"] == str(absolute_path)
