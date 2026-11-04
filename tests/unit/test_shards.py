"""DST-31: the suite can be split into equal slices, one per CI job.

TEST_SHARD=N/M keeps the Nth of M slices (`pytest_deal_out_slices` in
tests/conftest.py). Together the slices must be exactly the whole suite: a test in no
slice would never run in CI, and a test in two would run twice. The hook is called
directly here with stand-in items, which is instant; that the real suite splits the
same way is checked in test_slow_registry.py.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from tests.conftest import pytest_deal_out_slices

IDS = [f"tests/unit/test_{f}.py::test_{n}[{p}]" for f in "abcde" for n in range(7) for p in range(3)]


def _deal(ids, spec, monkeypatch):
    if spec is None:
        monkeypatch.delenv("TEST_SHARD", raising=False)
    else:
        monkeypatch.setenv("TEST_SHARD", spec)
    items = [SimpleNamespace(nodeid=i) for i in ids]
    dropped: list = []
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda items: dropped.extend(items)))
    pytest_deal_out_slices(config, items)
    return [i.nodeid for i in items], [i.nodeid for i in dropped]


@pytest.mark.parametrize("total", [1, 2, 3, 4, 7])
def test_the_slices_together_are_exactly_the_whole_suite_with_no_overlap(total, monkeypatch):
    slices = [_deal(IDS, f"{n}/{total}", monkeypatch)[0] for n in range(1, total + 1)]

    assert sorted(sum(slices, [])) == sorted(IDS)              # every test once, none lost
    assert len({t for s in slices for t in s}) == len(IDS)     # nothing in two slices
    sizes = [len(s) for s in slices]
    assert max(sizes) - min(sizes) <= 1                        # equal in size


def test_what_a_slice_does_not_keep_it_reports_as_deselected(monkeypatch):
    kept, dropped = _deal(IDS, "2/4", monkeypatch)
    assert sorted(kept + dropped) == sorted(IDS)


def test_a_slice_is_the_same_whatever_order_the_tests_were_collected_in(monkeypatch):
    shuffled = IDS[:]
    random.Random(7).shuffle(shuffled)
    for spec in ("1/3", "2/3", "3/3"):
        assert sorted(_deal(IDS, spec, monkeypatch)[0]) == sorted(_deal(shuffled, spec, monkeypatch)[0])


def test_a_slice_keeps_the_order_the_tests_were_collected_in(monkeypatch):
    kept, _ = _deal(IDS, "1/3", monkeypatch)
    assert kept == [i for i in IDS if i in set(kept)]


def test_one_slice_of_one_and_no_slicing_at_all_keep_everything(monkeypatch):
    assert _deal(IDS, "1/1", monkeypatch)[0] == IDS
    assert _deal(IDS, None, monkeypatch) == (IDS, [])


def test_more_slices_than_tests_leaves_some_empty_and_loses_nothing(monkeypatch):
    few = IDS[:3]
    slices = [_deal(few, f"{n}/5", monkeypatch)[0] for n in range(1, 6)]
    assert sorted(sum(slices, [])) == sorted(few)


@pytest.mark.parametrize("spec", ["0/3", "4/3", "x", "1/0", "2/", "/3", "1-3", "-1/3", "1/2/3"])
def test_a_malformed_slice_is_refused_rather_than_running_nothing(spec, monkeypatch):
    with pytest.raises(pytest.UsageError, match="TEST_SHARD must look like"):
        _deal(IDS, spec, monkeypatch)
