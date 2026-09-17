from shikiru.bootstrap import build_clock
from shikiru.clock import SystemClock


def test_build_clock_returns_a_system_clock() -> None:
    assert isinstance(build_clock(), SystemClock)
