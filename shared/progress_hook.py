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
from typing import Callable, Optional

# (step, done, total) -> None. Steps: voices, portraits, pictures, shots, cut.
Sink = Callable[[str, int, int], None]
_sink: contextvars.ContextVar[Optional[Sink]] = contextvars.ContextVar("progress_sink", default=None)


def listen(sink: Optional[Sink]) -> contextvars.Token:
    return _sink.set(sink)


def stop(token: contextvars.Token) -> None:
    _sink.reset(token)


def report(step: str, done: int, total: int) -> None:
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(step, done, total)
    except Exception:  # noqa: BLE001 — a progress line must never fail a render
        pass
