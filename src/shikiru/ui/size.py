"""Human-readable byte counts."""

from __future__ import annotations

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")
_UNIT_STEP = 1024


def format_size(size_bytes: int) -> str:
    """Return `size_bytes` in the largest binary unit that keeps it at or above one."""
    value = float(size_bytes)
    unit = _UNITS[0]
    for unit in _UNITS:
        if value < _UNIT_STEP or unit == _UNITS[-1]:
            break
        value /= _UNIT_STEP
    return f"{value:.0f} {unit}" if unit == _UNITS[0] else f"{value:.1f} {unit}"
