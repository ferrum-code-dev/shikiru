from __future__ import annotations

import pytest

from shikiru.domain.caches import (
    CacheEntryEffect,
    detected_caches,
    ineffective_cache_entry,
    uncached_detections,
)


@pytest.mark.parametrize(
    ("ignored", "expected"),
    [
        pytest.param(["node_modules", "dist"], ("node_modules",), id="known-name"),
        pytest.param(["web/node_modules", ".venv"], (".venv", "web/node_modules"), id="nested"),
        pytest.param(["build", "notes.log"], (), id="unknown-names"),
    ],
)
def test_detection_reports_ignored_paths_with_a_known_cache_name(
    ignored: list[str], expected: tuple[str, ...]
) -> None:
    assert detected_caches(ignored) == expected


@pytest.mark.parametrize(
    ("detected", "copied", "expected"),
    [
        pytest.param(
            ("node_modules", ".venv"), set[str](), ("node_modules", ".venv"), id="nothing-copied"
        ),
        pytest.param(("node_modules", ".venv"), {"node_modules"}, (".venv",), id="partly"),
        pytest.param(("node_modules",), {"node_modules"}, (), id="all-copied"),
    ],
)
def test_uncached_detections_are_the_detected_paths_no_cache_pattern_matches(
    detected: tuple[str, ...], copied: set[str], expected: tuple[str, ...]
) -> None:
    assert uncached_detections(detected, copied) == expected


@pytest.mark.parametrize(
    ("matches", "ignored", "expected"),
    [
        pytest.param([], {"dist"}, CacheEntryEffect.MATCHES_NOTHING, id="matches-nothing"),
        pytest.param(["dist"], set[str](), CacheEntryEffect.NOT_GIT_IGNORED, id="not-ignored"),
        pytest.param(["dist", "out"], {"out"}, None, id="one-ignored-match-takes-effect"),
        pytest.param(["node_modules"], {"node_modules"}, None, id="ignored-match"),
    ],
)
def test_cache_entry_has_no_effect_when_none_of_its_matches_is_git_ignored(
    matches: list[str], ignored: set[str], expected: CacheEntryEffect | None
) -> None:
    assert ineffective_cache_entry(matches, ignored) == expected
