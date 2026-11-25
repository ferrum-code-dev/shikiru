"""Unit tests for the pure test shard rule."""

from __future__ import annotations

import pytest

from tests.support.shards import SHARD_OPTION, Shard, parse_shard


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("1/3", Shard(1, 3), id="first"),
        pytest.param("3/3", Shard(3, 3), id="last"),
        pytest.param("0/3", ValueError, id="below-first-rejected"),
        pytest.param("4/3", ValueError, id="beyond-count-rejected"),
        pytest.param("1", ValueError, id="missing-count-rejected"),
        pytest.param("a/3", ValueError, id="non-integer-rejected"),
    ],
)
def test_a_shard_is_an_index_within_its_count(
    text: str, expected: Shard | type[ValueError]
) -> None:
    if expected is ValueError:
        with pytest.raises(ValueError, match=SHARD_OPTION):
            parse_shard(text)
    else:
        assert parse_shard(text) == expected


def test_shards_partition_the_suite_evenly_whatever_the_collection_order() -> None:
    test_ids = [f"test_file.py::test_{number}" for number in range(10)]

    parts = [Shard(index, 3).select(test_ids) for index in (1, 2, 3)]

    assert sorted(test_id for part in parts for test_id in part) == sorted(test_ids)
    assert sorted(len(part) for part in parts) == [3, 3, 4]
    assert parts == [Shard(index, 3).select(test_ids[::-1]) for index in (1, 2, 3)]
