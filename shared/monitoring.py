"""Watching the live site: the checks the monitor service runs, and how it tells you.

`python main.py monitor` runs `run_forever`. Every `loop_s` seconds it looks at
the job queue, the API's request log and the disk, decides for each check
whether there is a problem, and reports that to Healthchecks.io, which emails
the owner when a check changes state. The thresholds are in
config/monitoring.yaml, each with its reason.

One check per kind of problem, so the email's subject alone says what broke
(`dastango-job-silent`, `dastango-disk`, ...). Each check is created by hand in
Healthchecks.io under that name (docs/MONITORING.md); the monitor reaches it at
`$MONITOR_PING_BASE/<name>` (and `/fail` when there is a problem). The base
holds the project's ping key, which is a secret: it lives in the VM's .env only
and is never logged.

The decisions (`judge_*`) are pure functions of facts handed to them, so every
rule and its edge cases is tested without a database, a clock or a network.
"""
from __future__ import annotations
import os
import shutil
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml
from sqlalchemy import select, text

from shared import constants, db
from shared.utils.logging import get_logger

log = get_logger("monitor")

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "monitoring.yaml"

# The Healthchecks.io check names. A check that is not in the monitor's results
# is never pinged, so a missing fact never reads as "all well".
CHECK_PREFIX = "dastango-"
CHECKS = ("monitor", "database", "job-silent", "job-slow", "queue-waiting",
          "job-errors", "api-errors", "disk", "disk-critical")

# A container health check for the monitor itself (it serves no HTTP).
ALIVE_FILE = Path(os.getenv("MONITOR_ALIVE_FILE")
                  or Path(tempfile.gettempdir()) / "storygen-monitor-alive")


# ---- thresholds -------------------------------------------------------------

@dataclass(frozen=True)
class Thresholds:
    loop_s: float
    repeat_ping_s: float
    silent_after_s: float
    running_too_long_s: float
    queued_unclaimed_s: float
    error_window_s: float
    error_min_failures: int
    error_rate: float
    api_window_s: float
    api_min_errors: int
    api_error_rate: float
    warn_percent: float
    critical_percent: float


def load_thresholds(path: Optional[Path | str] = None) -> Thresholds:
    """Read the YAML. A missing or nonsensical number fails loudly, naming it."""
    target = Path(path or os.getenv("MONITORING_FILE") or DEFAULT_CONFIG_PATH)
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}

    def number(section: str, key: str, kind=float, high: Optional[float] = None):
        where = f"{section}.{key}" if section else key
        raw = (data.get(section) if section else data)
        raw = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{target.name}: {where} must be a number, got {raw!r}")
        if raw <= 0 or (high is not None and raw > high):
            raise ValueError(f"{target.name}: {where} must be above 0"
                             + (f" and at most {high}" if high is not None else "") + f", got {raw}")
        return kind(raw)

    t = Thresholds(
        loop_s=number("", "loop_s"),
        repeat_ping_s=number("", "repeat_ping_s"),
        silent_after_s=number("jobs", "silent_after_s"),
        running_too_long_s=number("jobs", "running_too_long_s"),
        queued_unclaimed_s=number("jobs", "queued_unclaimed_s"),
        error_window_s=number("jobs", "error_window_s", high=86400),
        error_min_failures=number("jobs", "error_min_failures", int),
        error_rate=number("jobs", "error_rate", high=1),
        api_window_s=number("api", "window_s", high=86400),
        api_min_errors=number("api", "min_errors", int),
        api_error_rate=number("api", "error_rate", high=1),
        warn_percent=number("disk", "warn_percent", high=100),
        critical_percent=number("disk", "critical_percent", high=100),
    )
    if t.warn_percent >= t.critical_percent:
        raise ValueError(f"{target.name}: disk.warn_percent must be below disk.critical_percent")
    if t.repeat_ping_s >= 600:
        # The `dastango-monitor` check expects a ping every 10 minutes.
        raise ValueError(f"{target.name}: repeat_ping_s must stay under 600 s, "
                         "or the monitor's own check goes down between pings")
    return t


# ---- the facts --------------------------------------------------------------

@dataclass
class JobRow:
    """The columns of a job the checks read."""
    id: str
    kind: str
    status: str
    created_at: Optional[datetime] = None
    run_after: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None


Results = Dict[str, Optional[str]]   # check -> None when well, else what is wrong


def utcnow() -> datetime:
    """Naive UTC, the clock the job table uses on SQLite and Postgres alike."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def collect_jobs(now: datetime, window_s: float) -> List[JobRow]:
    """Unfinished jobs, and those that ended for good inside the window."""
    cutoff = now - timedelta(seconds=window_s)
    j = db.jobs
    stmt = select(j.c.id, j.c.kind, j.c.status, j.c.created_at, j.c.run_after,
                  j.c.started_at, j.c.finished_at, j.c.heartbeat_at).where(
        j.c.status.in_(("queued", "running"))
        | (j.c.status.in_(("succeeded", "failed")) & (j.c.finished_at >= cutoff)))
    with db.get_engine().connect() as c:
        return [JobRow(**dict(r)) for r in c.execute(stmt).mappings().all()]


def check_database() -> None:
    """Raises when the database cannot be reached."""
    with db.get_engine().connect() as c:
        c.execute(text("SELECT 1"))


# ---- the decisions ----------------------------------------------------------

def _span(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


def _names(rows: Iterable[JobRow], limit: int = 3) -> str:
    rows = list(rows)
    shown = ", ".join(f"{r.id} ({r.kind})" for r in rows[:limit])
    return shown + (f" and {len(rows) - limit} more" if len(rows) > limit else "")


def judge_jobs(rows: List[JobRow], now: datetime, t: Thresholds) -> Results:
    running = [r for r in rows if r.status == "running"]

    def last_seen(r: JobRow) -> datetime:
        return r.heartbeat_at or r.started_at or r.created_at or now

    silent = [r for r in running if (now - last_seen(r)).total_seconds() > t.silent_after_s]
    alive = [r for r in running if r not in silent]
    results: Results = {"job-silent": None, "job-slow": None, "queue-waiting": None,
                        "job-errors": None}

    if silent:
        longest = max((now - last_seen(r)).total_seconds() for r in silent)
        results["job-silent"] = (
            f"{_names(silent)} has not checked in for {_span(longest)}. The worker is "
            "probably down; a worker that is up gives the job back after 2 minutes.")

    # A job that is still checking in is only slow, never silent.
    slow = [r for r in alive
            if (now - (r.started_at or r.created_at or now)).total_seconds() > t.running_too_long_s]
    if slow:
        longest = max((now - (r.started_at or r.created_at or now)).total_seconds() for r in slow)
        results["job-slow"] = (f"{_names(slow)} has been running for {_span(longest)}, "
                               f"far past the {_span(t.running_too_long_s)} limit.")

    # Waiting is fine while something is making progress: the worker is busy.
    if not alive:
        def waited(r: JobRow) -> float:
            since = max(d for d in (r.created_at, r.run_after, now - timedelta(days=3650))
                        if d is not None)
            return (now - since).total_seconds()
        stuck = [r for r in rows if r.status == "queued" and waited(r) > t.queued_unclaimed_s]
        if stuck:
            results["queue-waiting"] = (
                f"{_names(stuck)} has been waiting {_span(max(waited(r) for r in stuck))} "
                "and nothing is running, so no worker is taking jobs.")

    window_start = now - timedelta(seconds=t.error_window_s)
    finished = [r for r in rows if r.status in ("succeeded", "failed")
                and r.finished_at is not None and r.finished_at >= window_start]
    failed = [r for r in finished if r.status == "failed"]
    if (len(failed) >= t.error_min_failures
            and len(failed) / len(finished) > t.error_rate):
        results["job-errors"] = (
            f"{len(failed)} of the last {len(finished)} jobs in {_span(t.error_window_s)} "
            f"failed ({_names(failed)}).")
    return results


def judge_api(entries: List[Tuple[float, int]], now: float, t: Thresholds) -> Optional[str]:
    """`entries` are (unix time, status) for requests the API answered."""
    recent = [status for ts, status in entries if now - ts <= t.api_window_s]
    errors = sum(1 for status in recent if status >= 500)
    if errors >= t.api_min_errors and errors / len(recent) > t.api_error_rate:
        return (f"{errors} of {len(recent)} API requests in the last "
                f"{_span(t.api_window_s)} ended in a server error (status 500 or more).")
    return None


def judge_disk(percent: float, t: Thresholds) -> Results:
    results: Results = {"disk": None, "disk-critical": None}
    if percent >= t.critical_percent:
        results["disk-critical"] = (f"The data disk is {percent:.0f}% full "
                                    f"(critical from {t.critical_percent:.0f}%).")
    if percent >= t.warn_percent:
        results["disk"] = (f"The data disk is {percent:.0f}% full "
                           f"(warning from {t.warn_percent:.0f}%).")
    return results


# ---- the API's request log --------------------------------------------------
# One line per answered request, "<unix seconds> <status>", in a file per UTC
# day under DATA_DIR/monitoring. The API appends, the monitor reads: they share
# the data volume, and no request, path, address or header is ever written.

def request_log_dir() -> Path:
    return constants.DATA_DIR / "monitoring"


def _log_file(day: datetime) -> Path:
    return request_log_dir() / f"requests-{day:%Y%m%d}.log"


def log_request(status: int, at: Optional[float] = None) -> None:
    """Called for every answered request. Failing to log must never fail the request."""
    at = time.time() if at is None else at
    try:
        path = _log_file(datetime.fromtimestamp(at, timezone.utc))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="ascii") as f:
            f.write(f"{int(at)} {int(status)}\n")
    except OSError:
        pass


def read_requests(now: float, window_s: float) -> List[Tuple[float, int]]:
    """Requests inside the window, from today's file and yesterday's."""
    today = datetime.fromtimestamp(now, timezone.utc)
    entries: List[Tuple[float, int]] = []
    for day in (today - timedelta(days=1), today):
        try:
            lines = _log_file(day).read_text(encoding="ascii", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
                continue   # a half-written line, from an append caught mid-write
            if now - int(parts[0]) <= window_s:
                entries.append((float(parts[0]), int(parts[1])))
    return entries


def prune_request_logs(now: float, keep_days: int = 2) -> None:
    """Delete day files older than the ones the monitor can still read."""
    keep = {_log_file(datetime.fromtimestamp(now, timezone.utc) - timedelta(days=d)).name
            for d in range(keep_days)}
    try:
        for path in request_log_dir().glob("requests-*.log"):
            if path.name not in keep:
                path.unlink(missing_ok=True)
    except OSError:
        pass


# ---- telling the owner ------------------------------------------------------

class Pinger:
    """Sends one check's state to Healthchecks.io. Never raises, never logs the URL."""

    def __init__(self, base: str, timeout_s: float = 10.0,
                 opener: Callable[..., Any] = urllib.request.urlopen):
        self.base = base.rstrip("/")
        self.timeout_s = timeout_s
        self._open = opener

    def send(self, check: str, message: Optional[str]) -> bool:
        url = f"{self.base}/{CHECK_PREFIX}{check}" + ("" if message is None else "/fail")
        body = (message or "").encode("utf-8")[:1000]
        try:
            request = urllib.request.Request(url, data=body, method="POST")
            with self._open(request, timeout=self.timeout_s) as response:
                return 200 <= response.status < 300
        except Exception as e:  # noqa: BLE001
            # The class name only: an error can quote the URL, which holds the key.
            log.warning("could not report %s (%s)", check, type(e).__name__)
            return False


class Reporter:
    """Sends a check when its state changes and otherwise every `repeat_s`."""

    def __init__(self, pinger: Pinger, repeat_s: float, clock: Callable[[], float] = time.monotonic):
        self.pinger = pinger
        self.repeat_s = repeat_s
        self._clock = clock
        self._state: Dict[str, Optional[str]] = {}
        self._sent_at: Dict[str, float] = {}

    def report(self, results: Results) -> None:
        now = self._clock()
        for check, message in results.items():
            changed = check not in self._state or (self._state[check] is None) != (message is None)
            if not changed and now - self._sent_at.get(check, -1e12) < self.repeat_s:
                continue
            if self.pinger.send(check, message):
                self._state[check] = message
                self._sent_at[check] = now
                if changed:
                    log.info("%s: %s", check, "ok" if message is None else message)


# ---- the loop ---------------------------------------------------------------

def evaluate(t: Thresholds, now: Optional[datetime] = None,
             wall: Optional[float] = None) -> Results:
    """Look at everything once. One check failing to run does not stop the others."""
    now = now or utcnow()
    wall = time.time() if wall is None else wall
    results: Results = {}

    try:
        check_database()
        rows = collect_jobs(now, t.error_window_s)
        results["database"] = None
        results.update(judge_jobs(rows, now, t))
    except Exception as e:  # noqa: BLE001
        # The class name only: a database error can quote the connection URL.
        results["database"] = f"The database could not be reached ({type(e).__name__})."
        log.warning("database check failed (%s)", type(e).__name__)

    try:
        results["api-errors"] = judge_api(read_requests(wall, t.api_window_s), wall, t)
    except Exception as e:  # noqa: BLE001
        results["api-errors"] = f"The API request log could not be read ({type(e).__name__})."

    try:
        usage = shutil.disk_usage(constants.DATA_DIR)
        results.update(judge_disk(usage.used / usage.total * 100, t))
    except Exception as e:  # noqa: BLE001
        results["disk"] = f"The disk could not be measured ({type(e).__name__})."
    return results


def mark_alive() -> None:
    try:
        ALIVE_FILE.touch()
    except OSError:
        pass


def run_forever(t: Thresholds, pinger: Optional[Pinger],
                stop: Optional[threading.Event] = None) -> None:
    """Evaluate and report until told to stop. Without a pinger it only logs."""
    stop = stop or threading.Event()
    reporter = Reporter(pinger, t.repeat_ping_s) if pinger else None
    log.info("monitor up (every %.0f s; %s)", t.loop_s,
             "reporting to Healthchecks.io" if pinger else
             "MONITOR_PING_BASE is not set, so problems are only logged")
    last_prune = 0.0
    while not stop.is_set():
        results = evaluate(t)
        results["monitor"] = None          # reaching this line is the heartbeat
        for check, message in results.items():
            if message is not None:
                log.warning("problem: %s: %s", check, message)
        if reporter:
            reporter.report(results)
        mark_alive()
        if time.time() - last_prune > 3600:
            last_prune = time.time()
            prune_request_logs(last_prune)
        stop.wait(t.loop_s)
    log.info("monitor stopped")
