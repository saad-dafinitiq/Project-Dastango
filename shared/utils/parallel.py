"""Small helper for running independent provider calls concurrently.

How many run at once comes from the provider config (`concurrency:`), because
the limit belongs to the provider: Cloudflare is happy with several at a time,
the keyless Pollinations endpoint answers one request per IP, and a local GPU
pipeline must stay at one.
"""
from __future__ import annotations
import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Sequence, Tuple

from shared import progress_hook
from shared.utils.logging import get_logger

log = get_logger("parallel")

Job = Tuple[Callable[..., Any], Sequence[Any]]


def run_jobs(jobs: List[Job], workers: int, label: str = "", step: str = "") -> List[Any]:
    """Run (fn, args) jobs with at most `workers` at a time, keeping their order.

    `step` names what the jobs are for ("shots", "voices"): each finished one is
    reported to whoever is following the job (shared/progress_hook.py).
    """
    total = len(jobs)
    if step:
        done_count = [0]
        lock = threading.Lock()
        inner = jobs

        def counted(fn: Callable[..., Any]) -> Callable[..., Any]:
            def run(*args: Any) -> Any:
                result = fn(*args)
                with lock:
                    done_count[0] += 1
                    progress_hook.report(step, done_count[0], total)
                return result
            return run

        jobs = [(counted(fn), args) for fn, args in inner]
        progress_hook.report(step, 0, total)
    if workers <= 1 or len(jobs) <= 1:
        return [fn(*args) for fn, args in jobs]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Threads do not inherit context variables, so each job runs in a copy
        # of the caller's: that is how a model call in a worker thread knows
        # which job it belongs to (shared/tracing.py). One copy per job,
        # because a context cannot be entered by two threads at once.
        futures = [pool.submit(contextvars.copy_context().run, fn, *args) for fn, args in jobs]
        results = [f.result() for f in futures]
    log.info("%s: %d job(s) on %d workers in %.1fs", label or "batch", len(jobs), workers,
             time.monotonic() - started)
    return results
