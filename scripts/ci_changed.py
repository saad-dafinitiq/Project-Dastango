"""Does a pull request change anything but documentation?

Reads the changed file names, one per line, on stdin and prints `true` when the
test jobs should run and `false` when every changed file is documentation: under
docs/ or a Markdown file. Nothing in the test suite reads either. An empty list,
a workflow file or anything else prints `true`, so doubt always runs the tests.

Used by the `changes` job of .github/workflows/tests.yml. Standard library only,
so that job needs no Python setup.
"""
from __future__ import annotations

import sys
from typing import Iterable


def is_documentation(path: str) -> bool:
    path = path.strip().replace("\\", "/")
    return path.startswith("docs/") or path.endswith(".md")


def run_tests(changed: Iterable[str]) -> bool:
    files = [f for f in (c.strip() for c in changed) if f]
    return not files or not all(is_documentation(f) for f in files)


if __name__ == "__main__":
    print("true" if run_tests(sys.stdin.read().splitlines()) else "false")
