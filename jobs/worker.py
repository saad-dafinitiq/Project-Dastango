"""The worker: claims jobs and runs the pipeline.

Runs either as its own process (`python main.py worker`) or as a thread inside
the API process (the default, so `main.py serve` alone still works).

Cancellation reaches the work two ways. Between steps the orchestrator emits a progress event and checks
the job; and a command that is running (an ffmpeg shot, a look, the cut) is watched by `shared/procs.py`
and killed within a fraction of a second of the Stop, its half-written file removed (DST-44). No thread is
killed: the cancel is a BaseException that travels up through the work, and the edit puts the saved version's
files back.
"""
from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional, Sequence

from agents.orchestrator import ProgressEvent
from shared import tracing
from shared.utils.logging import get_logger

from . import queue

log = get_logger("worker")

# kind -> orchestrator method. Every one takes project_id and on_event as
# keyword arguments; the rest of the call is the job's payload.
METHODS = {
    "run_full": "run_full",
    "plan": "plan",
    "render": "render",
    "rerun_phase": "re_run_phase",
    "edit": "edit",
    "revert": "revert",
    "shot": "set_shot",
    "respec": "respec",
    "op": "apply_op",
    "ops": "apply_ops",
}

HEARTBEAT_S = 15.0
# How often a running job is asked whether it was told to stop. A heartbeat (a write) every HEARTBEAT_S is
# slow for that: a Stop pressed during an ffmpeg waited for the next beat or the next step.
CANCEL_POLL_S = 1.0

# A worker serves no HTTP, so its container health check reads this file
# instead: the idle loop touches it every poll, the job heartbeat every 15 s.
ALIVE_FILE = Path(os.getenv("WORKER_ALIVE_FILE")
                  or Path(tempfile.gettempdir()) / "storygen-worker-alive")


def mark_alive() -> None:
    try:
        ALIVE_FILE.touch()
    except OSError:  # a health signal must never stop the work
        pass


class JobCancelled(BaseException):
    """Raised inside a running job when someone asks it to stop.

    It derives from BaseException, not Exception, for the same reason
    KeyboardInterrupt does: the pipeline is full of `except Exception` blocks
    that log a failure and carry on, and a deliberate stop is not a failure.
    Catching it there made a cancelled run report an error to the UI first.
    """


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:4]}"


class _Heartbeat:
    """Checks in while a job runs, and notices a cancel request between events."""

    def __init__(self, job_id: str, interval: float = HEARTBEAT_S):
        self.job_id = job_id
        self.interval = interval
        self.cancelled = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"heartbeat-{job_id}")

    def _loop(self) -> None:
        last_beat = time.monotonic()
        while not self._stop.wait(min(self.interval, CANCEL_POLL_S)):
            try:
                if time.monotonic() - last_beat >= self.interval:
                    last_beat = time.monotonic()
                    mark_alive()
                    if not queue.heartbeat(self.job_id):
                        self.cancelled.set()
                elif queue.cancel_requested(self.job_id):
                    self.cancelled.set()
            except Exception:  # noqa: BLE001 — a flaky DB must not kill the run
                log.debug("heartbeat failed for %s", self.job_id, exc_info=True)

    def __enter__(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()


def _event_sink(job: queue.Job, beat: _Heartbeat) -> Callable[[ProgressEvent], None]:
    def check() -> None:
        if beat.cancelled.is_set() or not queue.heartbeat(job.id):
            raise JobCancelled(f"{job.id} cancelled")

    def push(ev: ProgressEvent) -> None:
        queue.append_event(job.id, job.project_id, phase=ev.phase,
                           status=ev.status, message=ev.message,
                           progress=ev.progress, payload=ev.payload)
        check()
    # Work that goes a long time between events calls this (shared/progress_hook.py `pulse`).
    push.check_cancelled = check  # type: ignore[attr-defined]
    # ...and a command that is running asks this, many times a second, without touching the database.
    push.peek_cancelled = beat.cancelled.is_set  # type: ignore[attr-defined]
    return push


def _owner(project_id: str) -> Optional[str]:
    """The film owner's internal account id for the trace, never their email.
    A lookup that fails just leaves the trace without a user."""
    try:
        from auth.accounts import owner_of
        return owner_of(project_id)
    except Exception:  # noqa: BLE001 — tracing detail, must not fail the job
        log.debug("no owner for %s", project_id, exc_info=True)
        return None


def run_job(job: queue.Job, orchestrator=None) -> str:
    """Run one claimed job to completion. Returns its final status."""
    from backend.services.pipeline_service import orchestrator as shared_orchestrator
    orch = orchestrator or shared_orchestrator()
    method = getattr(orch, METHODS[job.kind])
    log.info("job %s (%s) started for %s", job.id, job.kind, job.project_id)

    with _Heartbeat(job.id) as beat:
        push = _event_sink(job, beat)
        try:
            # Every model call made while the job runs is traced under it.
            with tracing.job_trace(job.id, job.project_id, job.kind,
                                   user_id=_owner(job.project_id),
                                   settings=job.payload) as trace:
                result = method(project_id=job.project_id, on_event=push,
                                **(job.payload or {}))
                trace.finish(tracing.summarize(result))
        except JobCancelled:
            queue.mark_cancelled(job.id)
            queue.append_event(job.id, job.project_id, phase="cancelled",
                               status="cancelled", message="cancelled", progress=1.0)
            log.info("job %s cancelled", job.id)
            return "cancelled"
        except Exception as e:  # noqa: BLE001 — a failed run must not kill the worker
            detail = f"{type(e).__name__}: {e}"
            log.exception("job %s failed", job.id)
            status = queue.fail(job.id, detail)
            queue.append_event(job.id, job.project_id, phase="error",
                               status="failed" if status == "failed" else "retrying",
                               message=detail, progress=1.0)
            return status

    try:
        _publish_assets(job)
    except Exception:  # noqa: BLE001 — the film exists; publishing is a bonus
        log.exception("job %s finished but its assets could not be published", job.id)
    queue.complete(job.id)
    log.info("job %s finished", job.id)
    return "succeeded"


def _publish_assets(job: queue.Job) -> None:
    """Hand the finished files to the asset store.

    A no-op on local disk. With object storage configured this is what makes a
    render done on this worker visible to an API process on another host.
    """
    from shared import assets
    if assets.backend_name() == "local":
        return
    from state_manager.snapshot import referenced_files
    from state_manager.state_manager import StateManager
    state = StateManager().latest(job.project_id)
    if state is None:
        return
    uploaded = assets.publish(referenced_files(state))
    log.info("published %d asset(s) to %s", len(uploaded), assets.backend_name())


def run_once(worker: Optional[str] = None,
             kinds: Optional[Sequence[str]] = None) -> Optional[queue.Job]:
    """Claim and run a single job. Returns None if the queue was empty."""
    job = queue.claim(worker or worker_id(), kinds=kinds)
    if job is None:
        return None
    run_job(job)
    return job


def run_forever(poll_interval: float = 1.0, kinds: Optional[Sequence[str]] = None,
                stop: Optional[threading.Event] = None,
                sweep_interval_s: float = 60.0) -> None:
    """Claim jobs until told to stop, recovering jobs abandoned by dead workers."""
    me = worker_id()
    stop = stop or threading.Event()
    log.info("worker %s up (kinds=%s)", me, ",".join(kinds) if kinds else "all")
    last_sweep = 0.0
    while not stop.is_set():
        now = time.monotonic()
        if now - last_sweep > sweep_interval_s:
            last_sweep = now
            try:
                recovered = queue.requeue_stale()
                if recovered:
                    log.warning("requeued %d job(s) from a stopped worker", recovered)
            except Exception:  # noqa: BLE001
                log.debug("stale sweep failed", exc_info=True)
        try:
            job = queue.claim(me, kinds=kinds)
        except Exception:  # noqa: BLE001 — e.g. the DB is briefly unavailable
            log.exception("could not claim a job")
            stop.wait(poll_interval)
            continue
        # Only a worker that can reach the queue counts as alive: one that
        # can't claim anything turns its container unhealthy, which is true.
        mark_alive()
        if job is None:
            stop.wait(poll_interval)
            continue
        try:
            run_job(job)
        except Exception:  # noqa: BLE001 — one bad job must not end the worker
            log.exception("job %s crashed outside the pipeline", job.id)
            queue.fail(job.id, "worker error, see the worker log")
    log.info("worker %s stopped", me)


def start_inline(poll_interval: float = 1.0) -> tuple[threading.Thread, threading.Event]:
    """Run a worker inside this process (what `main.py serve` does by default)."""
    stop = threading.Event()
    thread = threading.Thread(target=run_forever, kwargs={
        "poll_interval": poll_interval, "stop": stop,
    }, daemon=True, name="inline-worker")
    thread.start()
    return thread, stop
