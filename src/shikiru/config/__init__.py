"""Configuration: merge, validate and expose `Settings` (docs/design/05-data.md §1)."""

from __future__ import annotations

from shikiru.config.loader import load_settings
from shikiru.config.settings import Settings

__all__ = ["Settings", "load_settings"]
