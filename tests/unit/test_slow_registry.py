"""DST-31: the tests that pull requests skip (`slow`), and the slices they are dealt into.

A pull request runs `pytest -m "not slow"` in two slices (TEST_SHARD=1/2, 2/2). Three
things must hold or CI quietly checks less than it claims: every name in the SLOW
registry is a real test (a renamed test would silently rejoin the pull request, or
worse, a typo would never be marked), the marker selects exactly the registry, and
the two slices together are exactly what is not slow.
"""
from __future__ import annotations

import functools
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import SLOW

ROOT = Path(__file__).resolve().parents[2]


def _collect(*args: str, shard: str | None = None) -> list[str]:
    return list(_collected(args, shard))


@functools.lru_cache(maxsize=None)
def _collected(args: tuple, shard: str | None) -> tuple:
    """One real collection per distinct command, shared by every test in this file
    (each is a whole pytest start-up, about 3 s)."""
    env = {k: v for k, v in os.environ.items() if k != "TEST_SHARD"}
    if shard:
        env["TEST_SHARD"] = shard
    done = subprocess.run([sys.executable, "-m", "pytest", "tests", "--collect-only", "-q",
                           "-p", "no:cacheprovider", *args],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
    assert done.returncode == 0, done.stdout + done.stderr
    return tuple(line for line in done.stdout.splitlines() if "::" in line)


def _functions(ids: list[str]) -> set[str]:
    return {i.split("[")[0] for i in ids}


def test_the_slow_marker_selects_exactly_the_registry():
    marked = _functions(_collect("-m", "slow"))
    assert marked == set(SLOW), (
        f"in the registry but not collected: {sorted(set(SLOW) - marked)}; "
        f"marked but not in the registry: {sorted(marked - set(SLOW))}")


def test_what_a_pull_request_runs_has_none_of_the_slow_tests():
    assert not _functions(_collect("-m", "not slow")) & set(SLOW)


def test_the_two_pull_request_slices_are_exactly_what_is_not_slow():
    whole = _collect("-m", "not slow")
    first = _collect("-m", "not slow", shard="1/2")
    second = _collect("-m", "not slow", shard="2/2")

    assert len(whole) > 600                                   # a real suite, not an empty one
    assert sorted(first + second) == sorted(whole)            # every test once, none lost
    assert not set(first) & set(second)                       # none in both
    assert abs(len(first) - len(second)) <= 1                 # equal halves


def test_the_full_run_is_the_pull_request_run_plus_the_slow_tests():
    """What `pytest -q` (Gate 1, the weekly run) adds on top of a pull request."""
    everything = _collect()
    pull_request = _collect("-m", "not slow")
    slow = _collect("-m", "slow")
    assert sorted(pull_request + slow) == sorted(everything)
