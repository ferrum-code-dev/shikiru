"""Errors of a release update."""

from __future__ import annotations

from shikiru.errors import ShikiruError


class ReleaseStepError(ShikiruError):
    """An update step failed; the message names the step and the reason."""
