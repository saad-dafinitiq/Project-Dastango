"""Where a render is, in steps a creator can read (DST-38).

The pipeline used to say "recording the voices" and then nothing for four
minutes. The agents now report each finished unit of work ("3 of 12 shots") here,
and whoever is running the job (the orchestrator) decides what to do with it:
turn it into a progress event the browser follows.

A context variable carries the listener, so a report made from a worker thread
(`run_jobs` copies the caller's context into each job) reaches the job that
started it, and two jobs in one process never hear each other's work. With no
listener a report does nothing, which is every test and the command line.
"""
from __future__ import annotations

import contextvars
import time
from typing import Callable, Optional, Tuple

# (step, done, total) -> None. Steps: voices, portraits, pictures, shots, cut.
Sink = Callable[[str, int, int], None]
_sink: contextvars.ContextVar[Optional[Sink]] = contextvars.ContextVar("progress_sink", default=None)
# Called by `pulse()`: raises (JobCancelled) if the job has been told to stop, otherwise returns.
_check: contextvars.ContextVar[Optional[Callable[[], None]]] = contextvars.ContextVar("progress_check", default=None)
# Cheap and quick: has the job been told to stop? Read many times a second by a command that is running
# (`shared/procs.py`), so it must not touch the database; the worker answers from a flag it keeps fresh.
_peek: contextvars.ContextVar[Optional[Callable[[], bool]]] = contextvars.ContextVar("progress_peek", default=None)
PULSE_EVERY_S = 1.0
_last_pulse = [0.0]

Token = Tuple[contextvars.Token, contextvars.Token, contextvars.Token]


def listen(sink: Optional[Sink], check: Optional[Callable[[], None]] = None,
           peek: Optional[Callable[[], bool]] = None) -> Token:
    return _sink.set(sink), _check.set(check), _peek.set(peek)


def stop(token: Token) -> None:
    _sink.reset(token[0])
    _check.reset(token[1])
    _peek.reset(token[2])


def following() -> bool:
    """Is a job following the work in this context (so a command should watch for a Stop)?"""
    return _peek.get() is not None or _check.get() is not None


def cancel_requested() -> bool:
    """Has the job following this work been told to stop? False when nobody is following."""
    peek = _peek.get()
    if peek is not None:
        try:
            return bool(peek())
        except Exception:  # noqa: BLE001
            return False
    return False


def raise_if_cancelled() -> None:
    """Let the job's cancel travel up from here (it raises JobCancelled, a BaseException), now, not at most
    once a second as `pulse` does."""
    check = _check.get()
    if check is not None:
        check()


def pulse() -> None:
    """For work that runs a long time between reports (a look drawn frame by frame): let a Stop land.

    At most once a second it asks the job whether it was cancelled; a cancel is a BaseException
    and travels up through the work, which is how an edit stops mid-cut instead of finishing, saving
    a version, and only then being marked cancelled (DST-41). With no listener it does nothing."""
    check = _check.get()
    if check is None:
        return
    now = time.monotonic()
    if now - _last_pulse[0] < PULSE_EVERY_S:
        return
    _last_pulse[0] = now
    try:
        check()
    except Exception:  # noqa: BLE001 — a failing status check must never fail a render
        pass


def report(step: str, done: int, total: int) -> None:
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(step, done, total)
    except Exception:  # noqa: BLE001 — a progress line must never fail a render
        pass
