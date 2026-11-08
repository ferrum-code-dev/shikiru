"""The GitHub release listing is read as releases, and redirects stay on https."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from http.client import HTTPMessage

import pytest

from shikiru.update.errors import ReleaseStepError
from shikiru.update.github import HttpsRedirects, parse_releases

LISTING: list[object] = [
    {
        "tag_name": "v0.1.10",
        "draft": False,
        "prerelease": True,
        "assets": [
            {
                "name": "shikiru-0.1.10-py3-none-any.whl",
                "browser_download_url": "https://downloads.example.test/v0.1.10/wheel",
            }
        ],
    },
    {"tag_name": "v0.1.9", "draft": True, "prerelease": False, "assets": []},
]


def test_listing_gives_each_release_its_tag_flags_and_assets() -> None:
    releases = parse_releases(json.dumps(LISTING).encode())

    assert [(item.tag, item.draft, item.prerelease) for item in releases] == [
        ("v0.1.10", False, True),
        ("v0.1.9", True, False),
    ]
    assert releases[0].asset("shikiru-0.1.10-py3-none-any.whl") is not None
    assert releases[1].assets == ()


@pytest.mark.parametrize(
    "listing",
    [
        pytest.param(b"<html>rate limited</html>", id="not-json"),
        pytest.param(json.dumps([{"tag_name": "v0.1.0"}]).encode(), id="missing-fields"),
        pytest.param(json.dumps({"message": "Not Found"}).encode(), id="not-a-list"),
    ],
)
def test_listing_that_is_not_understood_is_refused(listing: bytes) -> None:
    with pytest.raises(ReleaseStepError, match="not understood"):
        parse_releases(listing)


def test_redirect_to_https_is_followed() -> None:
    request = HttpsRedirects().redirect_request(
        urllib.request.Request("https://api.example.test/start"),
        io.BytesIO(),
        302,
        "Found",
        HTTPMessage(),
        "https://objects.example.test/file",
    )

    assert request is not None
    assert request.full_url == "https://objects.example.test/file"


def test_redirect_to_plain_http_is_refused() -> None:
    with pytest.raises(urllib.error.URLError, match="not https"):
        HttpsRedirects().redirect_request(
            urllib.request.Request("https://api.example.test/start"),
            io.BytesIO(),
            302,
            "Found",
            HTTPMessage(),
            "http://objects.example.test/file",
        )
