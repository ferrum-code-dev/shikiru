"""Elapsed-time text shared by the CLI and the dashboard."""

from __future__ import annotations

from datetime import timedelta

AGE_NOW = "now"


def format_age(seconds: float) -> str:
    """Return `seconds` as `H:MM:SS` (with a day count past 24 hours)."""
    return str(timedelta(seconds=int(seconds)))
