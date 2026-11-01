from __future__ import annotations

import pytest

from shikiru.agents.settings import (
    AgentSetting,
    AgentSettingError,
    SettingKind,
    billing_declaration_setting,
    cli_args_setting,
    compact_threshold_setting,
)

_THRESHOLD = compact_threshold_setting(80.0)
_BILLING = billing_declaration_setting()
_ARGS = cli_args_setting(("--permission-mode", "bypassPermissions"))
_REQUIRED_CHOICE = AgentSetting(
    key="mode", label="Mode", help="", kind=SettingKind.CHOICE, default="a", choices=("a", "b")
)


@pytest.mark.parametrize(
    ("setting", "raw", "expected"),
    [
        (_THRESHOLD, "65", 65.0),
        (_THRESHOLD, "0", 0.0),
        (_THRESHOLD, "100", 100.0),
        (_BILLING, "", None),
        (_BILLING, "subscription", "subscription"),
        (_ARGS, "", []),
        (
            _ARGS,
            "--add-dir '/path with space' --flag=a\\ b",
            ["--add-dir", "/path with space", "--flag=a b"],
        ),
        (_REQUIRED_CHOICE, "b", "b"),
    ],
)
def test_accepted_text_becomes_the_value_written(
    setting: AgentSetting, raw: str, expected: object
) -> None:
    assert setting.parse(raw) == expected


@pytest.mark.parametrize(
    ("setting", "raw"),
    [
        (_THRESHOLD, ""),
        (_THRESHOLD, "abc"),
        (_THRESHOLD, "nan"),
        (_THRESHOLD, "inf"),
        (_THRESHOLD, "-1"),
        (_THRESHOLD, "100.5"),
        (_BILLING, "unknown"),
        (_REQUIRED_CHOICE, ""),
        (_ARGS, "--flag 'unterminated"),
    ],
)
def test_rejected_text_names_the_setting_to_correct(setting: AgentSetting, raw: str) -> None:
    with pytest.raises(AgentSettingError) as raised:
        setting.parse(raw)

    assert raised.value.key == setting.key
    assert setting.label in str(raised.value)


def test_argument_defaults_round_trip_through_their_editable_text() -> None:
    setting = cli_args_setting(("--add-dir", "a b", "--flag=x"))

    assert setting.parse(setting.display()) == ["--add-dir", "a b", "--flag=x"]
