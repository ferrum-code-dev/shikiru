from sample_backend import answer


def test_answer() -> None:
    assert answer() == 42
