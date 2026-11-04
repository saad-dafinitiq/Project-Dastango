"""DST-31: which pull requests skip the test jobs.

Only a change made entirely of documentation does. Every doubt (nothing listed,
a workflow, a file that merely looks like documentation) runs the tests, because
a skipped test job is a check that did not look.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_changed import run_tests

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("files", [
    ["docs/API.md"],
    ["docs/API.md", "docs/EVAL.md", "README.md"],
    ["deploy/README.md", "eval/README.md"],
    ["docs/mockups/creator.html"],                 # anything under docs/ is documentation
])
def test_a_change_of_only_documentation_skips_the_test_jobs(files):
    assert run_tests(files) is False


@pytest.mark.parametrize("files", [
    ["agents/story_agent/agent.py"],
    ["docs/API.md", "web/lib/types.ts"],           # one code file is enough
    ["docs/API.md", ".github/workflows/tests.yml"],
    ["pyproject.toml"],
    ["docs.py"],                                   # looks like docs, is code
    ["notes.md.py"],
    ["mydocs/readme.txt"],                         # a directory that only ends in docs
    [],                                            # nothing listed: do not guess
    ["", "  "],
])
def test_anything_else_runs_the_test_jobs(files):
    assert run_tests(files) is True


def test_windows_separators_and_stray_spaces_do_not_fool_it():
    assert run_tests(["docs\\API.md\n", " README.md "]) is False
    assert run_tests(["web\\lib\\types.ts"]) is True


def test_the_script_answers_true_or_false_on_standard_input():
    """What the workflow actually runs: the changed names piped in."""
    def answer(text: str) -> str:
        done = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "ci_changed.py")],
                              input=text, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    assert answer("docs/API.md\nREADME.md\n") == "false"
    assert answer("docs/API.md\nagents/orchestrator/workflow.py\n") == "true"
    assert answer("") == "true"
