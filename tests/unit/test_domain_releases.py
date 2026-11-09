"""Release rules: releases compare by number; a wheel's checksum comes from its listing."""

from __future__ import annotations

import pytest

from shikiru.domain.releases import (
    Release,
    ReleaseRuleError,
    checksum_for,
    newest_release,
    parse_installed_number,
    parse_number,
)

WHEEL = "shikiru-0.1.10-py3-none-any.whl"
DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


def release(tag: str, *, draft: bool = False, prerelease: bool = False) -> Release:
    return Release(tag=tag, draft=draft, prerelease=prerelease, assets=())


@pytest.mark.parametrize(
    ("releases", "expected_tag"),
    [
        pytest.param([release("v0.1.9"), release("v0.1.10")], "v0.1.10", id="by-number"),
        pytest.param(
            [release("v0.1.10"), release("v0.1.9")], "v0.1.10", id="listing-order-ignored"
        ),
        pytest.param(
            [release("v0.1.9"), release("v0.2.0", draft=True)], "v0.1.9", id="drafts-skipped"
        ),
        pytest.param([release("v0.2.0", prerelease=True)], "v0.2.0", id="pre-releases-included"),
        pytest.param(
            [release("v0.1.9"), release("latest"), release("v1.2"), release("0.2.0")],
            "v0.1.9",
            id="only-major-minor-patch-tags",
        ),
        pytest.param([release("latest"), release("v0.1.0", draft=True)], None, id="none-usable"),
        pytest.param([], None, id="no-releases"),
    ],
)
def test_newest_release_is_the_highest_usable_number(
    releases: list[Release], expected_tag: str | None
) -> None:
    found = newest_release(releases)

    assert (found[1].tag if found else None) == expected_tag


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("0.1.10", (0, 1, 10), id="numbers"),
        pytest.param("v0.1.0", None, id="tag-prefix"),
        pytest.param("0.1", None, id="incomplete"),
        pytest.param("0.1.0.1", None, id="extra-part"),
        pytest.param("0.1.x", None, id="letters"),
    ],
)
def test_version_text_must_be_major_minor_patch(
    text: str, expected: tuple[int, int, int] | None
) -> None:
    assert parse_number(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("0.1.4", (0, 1, 4), id="release"),
        pytest.param("0.1.4.dev3+gabc", (0, 1, 4), id="development-build"),
        pytest.param("1.0.0rc1", (1, 0, 0), id="release-candidate"),
        pytest.param("v0.1.4", None, id="tag-prefix"),
        pytest.param("0.1.4.1", None, id="extra-part"),
        pytest.param("0.1", None, id="incomplete"),
    ],
)
def test_installed_version_takes_its_release_segment(
    text: str, expected: tuple[int, int, int] | None
) -> None:
    assert parse_installed_number(text) == expected


@pytest.mark.parametrize(
    "listing",
    [
        pytest.param(f"{DIGEST}  {WHEEL}\n", id="two-spaces"),
        pytest.param(f"{DIGEST} *{WHEEL}\n", id="binary-marker"),
        pytest.param(f"{OTHER_DIGEST}  other.tar.gz\n{DIGEST}  {WHEEL}\n", id="among-others"),
    ],
)
def test_checksum_is_the_digest_listed_for_the_wheel(listing: str) -> None:
    assert checksum_for(listing.encode(), WHEEL) == DIGEST


@pytest.mark.parametrize(
    ("listing", "reason"),
    [
        pytest.param(f"{DIGEST[:-1]}  {WHEEL}\n", "malformed line", id="short-digest"),
        pytest.param(f"{DIGEST}{WHEEL}\n", "malformed line", id="no-separator"),
        pytest.param(f"{DIGEST.upper()}  {WHEEL}\n", "malformed line", id="uppercase-digest"),
        pytest.param(
            f"{DIGEST}  {WHEEL}\nnot a checksum\n", "malformed line", id="bad-line-elsewhere"
        ),
        pytest.param(f"{OTHER_DIGEST}  other.tar.gz\n", "does not list", id="wheel-not-listed"),
        pytest.param(
            f"{DIGEST}  {WHEEL}\n{OTHER_DIGEST}  {WHEEL}\n", "more than once", id="listed-twice"
        ),
        pytest.param("", "does not list", id="empty"),
    ],
)
def test_checksum_listing_that_breaks_a_rule_is_refused(listing: str, reason: str) -> None:
    with pytest.raises(ReleaseRuleError, match=reason):
        checksum_for(listing.encode(), WHEEL)


def test_checksum_listing_must_be_utf8() -> None:
    with pytest.raises(ReleaseRuleError, match="not UTF-8"):
        checksum_for(b"\xff\xfe", WHEEL)
