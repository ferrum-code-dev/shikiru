from __future__ import annotations

from shikiru.workspace.roots import slugify


def test_slugify_is_stable_and_filesystem_safe() -> None:
    assert slugify("Add Retry to Parser!!") == "add-retry-to-parser"
    assert slugify("  --Weird///Title--  ") == "weird-title"
    assert slugify("a" * 100) == "a" * 40
