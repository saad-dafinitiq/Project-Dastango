"""DST-32: the monitor decides what is wrong, and tells the owner once.

The rules are pure functions of facts, so each is checked here with a clock and
a list of jobs: the edge of every threshold, and the cases the reasons in
config/monitoring.yaml were written for (a busy worker is not a dead one; one
failure on a quiet site is not an outage).
"""
from __future__ import annotations
import argparse
import logging
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from shared import db, monitoring
from shared.monitoring import JobRow, Pinger, Reporter

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 4, 12, 0, 0)
T = monitoring.load_thresholds()


def ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def running(id="job_a", heartbeat=10, started=60, kind="render") -> JobRow:
    return JobRow(id=id, kind=kind, status="running", created_at=ago(started + 5),
                  started_at=ago(started), heartbeat_at=None if heartbeat is None else ago(heartbeat))


def queued(id="job_q", waited=700, run_after_ago=None, kind="plan") -> JobRow:
    return JobRow(id=id, kind=kind, status="queued", created_at=ago(waited),
                  run_after=ago(waited if run_after_ago is None else run_after_ago))


def finished(status, ended=600, id="job_f") -> JobRow:
    return JobRow(id=id, kind="render", status=status, created_at=ago(ended + 100),
                  started_at=ago(ended + 90), finished_at=ago(ended))


def problems(results) -> set:
    return {check for check, message in results.items() if message}


# ---- thresholds: the file the reasons live in ------------------------------------------

def test_the_shipped_thresholds_are_the_ones_the_alerts_were_designed_around():
    assert (T.silent_after_s, T.running_too_long_s, T.queued_unclaimed_s) == (300, 1800, 600)
    assert (T.error_min_failures, T.error_rate, T.error_window_s) == (3, 0.5, 3600)
    assert (T.api_min_errors, T.api_error_rate, T.api_window_s) == (5, 0.05, 600)
    assert (T.warn_percent, T.critical_percent) == (80, 90)


def test_every_threshold_has_a_reason_written_next_to_it():
    # The ticket asks for the reason beside each number, not in a separate document.
    # Keys that share one explanation (the three error-rate numbers) may sit under one comment.
    lines = (ROOT / "config" / "monitoring.yaml").read_text(encoding="utf-8").splitlines()

    def is_value(line):
        return (not line.lstrip().startswith("#") and ":" in line
                and line.split(":", 1)[1].strip() != "")

    for number, line in enumerate(lines):
        if not is_value(line) or line.startswith("version"):
            continue
        above = number - 1
        while above >= 0 and is_value(lines[above]):
            above -= 1
        assert lines[above].lstrip().startswith("#"), f"no reason above {line.strip()!r}"


@pytest.mark.parametrize("edit, message", [
    (lambda d: d["jobs"].pop("silent_after_s"), "jobs.silent_after_s"),
    (lambda d: d["jobs"].update(error_rate=1.5), "jobs.error_rate"),
    (lambda d: d["api"].update(min_errors=0), "api.min_errors"),
    (lambda d: d["jobs"].update(silent_after_s="five minutes"), "jobs.silent_after_s"),
    (lambda d: d["jobs"].update(silent_after_s=True), "jobs.silent_after_s"),
    (lambda d: d["disk"].update(warn_percent=95), "warn_percent must be below"),
    (lambda d: d.update(repeat_ping_s=900), "repeat_ping_s"),
    (lambda d: d["api"].update(window_s=200000), "api.window_s"),
])
def test_a_wrong_threshold_fails_loudly_naming_itself(tmp_path, edit, message):
    data = yaml.safe_load((ROOT / "config" / "monitoring.yaml").read_text(encoding="utf-8"))
    edit(data)
    path = tmp_path / "monitoring.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        monitoring.load_thresholds(path)


def test_the_thresholds_file_can_be_replaced_from_the_environment(tmp_path, monkeypatch):
    data = yaml.safe_load((ROOT / "config" / "monitoring.yaml").read_text(encoding="utf-8"))
    data["jobs"]["silent_after_s"] = 123
    path = tmp_path / "other.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("MONITORING_FILE", str(path))
    assert monitoring.load_thresholds().silent_after_s == 123


# ---- jobs --------------------------------------------------------------------------------

def test_a_healthy_queue_raises_nothing():
    rows = [running(), finished("succeeded"), queued(waited=30)]
    assert problems(monitoring.judge_jobs(rows, NOW, T)) == set()


def test_a_job_whose_worker_stopped_checking_in_is_silent_not_slow():
    results = monitoring.judge_jobs([running(heartbeat=400, started=7200)], NOW, T)
    assert problems(results) == {"job-silent"}
    assert "job_a (render)" in results["job-silent"] and "7 min" in results["job-silent"]


def test_silence_is_judged_from_the_last_check_in_and_the_edge_is_exact():
    assert not problems(monitoring.judge_jobs([running(heartbeat=300)], NOW, T))
    assert "job-silent" in problems(monitoring.judge_jobs([running(heartbeat=301)], NOW, T))


def test_a_job_that_never_checked_in_is_judged_from_when_it_started():
    # Claimed a moment ago: fine. Claimed ten minutes ago with no beat: silent.
    assert not problems(monitoring.judge_jobs([running(heartbeat=None, started=20)], NOW, T))
    assert "job-silent" in problems(monitoring.judge_jobs([running(heartbeat=None, started=600)], NOW, T))


def test_a_long_job_that_is_still_alive_is_slow_not_silent():
    results = monitoring.judge_jobs([running(heartbeat=5, started=1900)], NOW, T)
    assert problems(results) == {"job-slow"}
    assert "32 min" in results["job-slow"]


def test_the_slow_limit_is_30_minutes_to_the_second():
    assert not problems(monitoring.judge_jobs([running(started=1800)], NOW, T))
    assert "job-slow" in problems(monitoring.judge_jobs([running(started=1801)], NOW, T))


def test_a_job_waiting_with_nothing_running_means_no_worker_is_taking_jobs():
    results = monitoring.judge_jobs([queued(waited=700)], NOW, T)
    assert problems(results) == {"queue-waiting"}
    assert "job_q (plan)" in results["queue-waiting"]


def test_a_job_waiting_behind_a_busy_worker_is_not_an_alarm():
    # One worker on the VM: a long render is a good reason to wait.
    rows = [running(heartbeat=3, started=400), queued(waited=3000)]
    assert not problems(monitoring.judge_jobs(rows, NOW, T))


def test_a_silent_job_does_not_excuse_the_queue_behind_it():
    rows = [running(heartbeat=900), queued(waited=3000)]
    assert problems(monitoring.judge_jobs(rows, NOW, T)) == {"job-silent", "queue-waiting"}


def test_a_job_that_just_failed_and_is_backing_off_has_not_been_waiting_long():
    # Created an hour ago, retried a few seconds ago: run_after is what counts.
    row = queued(waited=3600, run_after_ago=4)
    assert not problems(monitoring.judge_jobs([row], NOW, T))


def test_the_queue_wait_limit_is_ten_minutes_to_the_second():
    assert not problems(monitoring.judge_jobs([queued(waited=600)], NOW, T))
    assert "queue-waiting" in problems(monitoring.judge_jobs([queued(waited=601)], NOW, T))


def test_three_failures_and_most_jobs_failing_is_an_error_rate_alarm():
    rows = [finished("failed", id=f"job_{i}") for i in range(3)] + [finished("succeeded")]
    results = monitoring.judge_jobs(rows, NOW, T)
    assert problems(results) == {"job-errors"}
    assert "3 of the last 4 jobs" in results["job-errors"]


def test_one_or_two_failures_on_a_quiet_site_are_not_an_outage():
    for failures in (1, 2):
        rows = [finished("failed", id=f"job_{i}") for i in range(failures)]
        assert not problems(monitoring.judge_jobs(rows, NOW, T)), failures


def test_failures_among_many_successes_are_ordinary_weather():
    rows = [finished("failed", id=f"f{i}") for i in range(3)] + [
        finished("succeeded", id=f"s{i}") for i in range(7)]
    assert not problems(monitoring.judge_jobs(rows, NOW, T))


def test_exactly_half_failing_is_not_more_than_half():
    rows = [finished("failed", id=f"f{i}") for i in range(3)] + [
        finished("succeeded", id=f"s{i}") for i in range(3)]
    assert not problems(monitoring.judge_jobs(rows, NOW, T))


def test_failures_older_than_the_window_and_cancelled_jobs_do_not_count():
    old = [finished("failed", ended=7200, id=f"old{i}") for i in range(5)]
    cancelled = [JobRow(id=f"c{i}", kind="render", status="cancelled", finished_at=ago(60))
                 for i in range(5)]
    assert not problems(monitoring.judge_jobs(old + cancelled + [finished("succeeded")], NOW, T))


def test_cancelled_jobs_do_not_dilute_the_failure_rate():
    # Three of three finished jobs failed; the cancelled ones are neither.
    rows = [finished("failed", id=f"f{i}") for i in range(3)] + [
        JobRow(id=f"c{i}", kind="render", status="cancelled", finished_at=ago(60))
        for i in range(4)]
    results = monitoring.judge_jobs(rows, NOW, T)
    assert "job-errors" in problems(results) and "3 of the last 3 jobs" in results["job-errors"]


def test_the_message_names_a_few_jobs_and_counts_the_rest():
    rows = [running(id=f"job_{i}", heartbeat=900) for i in range(5)]
    message = monitoring.judge_jobs(rows, NOW, T)["job-silent"]
    assert "job_0 (render), job_1 (render), job_2 (render) and 2 more" in message


# ---- the API's error rate ---------------------------------------------------------------

def requests(ok, bad, age=60):
    now = 1_800_000_000.0
    return now, [(now - age, 200)] * ok + [(now - age, 503)] * bad


def test_a_burst_of_server_errors_is_an_alarm():
    now, entries = requests(ok=20, bad=8)
    message = monitoring.judge_api(entries, now, T)
    assert message and "8 of 28 API requests" in message


def test_a_few_errors_in_a_lot_of_traffic_are_not():
    now, entries = requests(ok=400, bad=8)           # 2%, under the 5% line
    assert monitoring.judge_api(entries, now, T) is None


def test_a_handful_of_errors_on_a_quiet_site_are_not():
    now, entries = requests(ok=0, bad=4)             # 100%, but only four
    assert monitoring.judge_api(entries, now, T) is None
    now, entries = requests(ok=0, bad=5)
    assert monitoring.judge_api(entries, now, T)


def test_old_errors_age_out_of_the_window_and_silence_is_not_an_error():
    now, entries = requests(ok=0, bad=50, age=900)   # a quarter of an hour ago
    assert monitoring.judge_api(entries, now, T) is None
    assert monitoring.judge_api([], now, T) is None


def test_4xx_answers_are_not_server_errors():
    now = 1_800_000_000.0
    assert monitoring.judge_api([(now - 5, 404)] * 50 + [(now - 5, 401)] * 50, now, T) is None


# ---- disk ----------------------------------------------------------------------------------

@pytest.mark.parametrize("percent, expected", [
    (50, set()), (79.9, set()), (80, {"disk"}), (89.9, {"disk"}), (90, {"disk", "disk-critical"}),
    (100, {"disk", "disk-critical"}),
])
def test_disk_warns_at_80_and_is_critical_at_90(percent, expected):
    assert problems(monitoring.judge_disk(percent, T)) == expected


# ---- the API's request log -----------------------------------------------------------------

def test_the_request_log_round_trips_status_codes_and_nothing_else(tmp_path):
    now = 1_800_000_000
    monitoring.log_request(200, at=now - 30)
    monitoring.log_request(503, at=now - 20)
    assert monitoring.read_requests(now, 600) == [(now - 30, 200), (now - 20, 503)]
    (logfile,) = (tmp_path / "monitoring").glob("requests-*.log")
    assert all(len(line.split()) == 2 for line in logfile.read_text().splitlines())


def test_the_window_reaches_back_across_midnight():
    midnight = int(datetime(2026, 11, 5, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    monitoring.log_request(500, at=midnight - 120)       # yesterday's file
    monitoring.log_request(200, at=midnight + 30)        # today's
    got = monitoring.read_requests(midnight + 60, 600)
    assert [status for _, status in got] == [500, 200]


def test_requests_older_than_the_window_are_not_returned():
    now = 1_800_000_000
    monitoring.log_request(500, at=now - 5000)
    monitoring.log_request(200, at=now - 10)
    assert monitoring.read_requests(now, 600) == [(now - 10, 200)]


def test_a_half_written_line_is_skipped_not_fatal(tmp_path):
    now = 1_800_000_000
    monitoring.log_request(200, at=now - 10)
    (logfile,) = (tmp_path / "monitoring").glob("requests-*.log")
    with open(logfile, "a") as f:
        # An append caught mid-write, a line from something else, and a status cut short.
        f.write("18000000\nabc def\n1800000000 x\n1800000000 -5\n1800000000 ")
    assert monitoring.read_requests(now, 600) == [(now - 10, 200)]


def test_old_day_files_are_deleted_and_the_two_the_monitor_reads_stay(tmp_path):
    now = 1_800_000_000.0
    for days_back in (0, 1, 2, 3):
        monitoring.log_request(200, at=now - days_back * 86400)
    monitoring.prune_request_logs(now)
    assert len(list((tmp_path / "monitoring").glob("requests-*.log"))) == 2


def test_failing_to_write_the_log_never_fails_the_request(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(monitoring, "request_log_dir", lambda: blocker / "monitoring")
    monitoring.log_request(200)               # raises nothing


# ---- what the monitor reads from the database --------------------------------------------

@pytest.fixture
def quiet_disk(monkeypatch):
    """Half-full, whatever this machine's disk really is."""
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(monitoring.shutil, "disk_usage", lambda path: usage(100, 50, 50))


def test_the_collector_returns_unfinished_jobs_and_recent_final_ones(isolated_dirs):
    from jobs import queue
    claimed = queue.enqueue("plan", "p-running")
    queue.claim("worker-1")                                # nothing older: takes this one
    keep_queued = queue.enqueue("plan", "p-queued")
    recent_fail = queue.enqueue("plan", "p-fail", max_attempts=1)
    old_fail = queue.enqueue("plan", "p-old", max_attempts=1)
    cancelled = queue.enqueue("plan", "p-cancel")
    queue.cancel(cancelled.id)
    ok = queue.enqueue("plan", "p-ok")
    now = monitoring.utcnow()
    with db.get_engine().begin() as c:
        c.execute(db.jobs.update().where(db.jobs.c.id == recent_fail.id).values(
            status="failed", finished_at=now - timedelta(minutes=5)))
        c.execute(db.jobs.update().where(db.jobs.c.id == old_fail.id).values(
            status="failed", finished_at=now - timedelta(hours=3)))
        c.execute(db.jobs.update().where(db.jobs.c.id == ok.id).values(
            status="succeeded", finished_at=now - timedelta(minutes=1)))
    rows = {r.id: r.status for r in monitoring.collect_jobs(now, 3600)}
    assert rows == {claimed.id: "running", keep_queued.id: "queued",
                    recent_fail.id: "failed", ok.id: "succeeded"}
    assert old_fail.id not in rows and cancelled.id not in rows


def test_evaluate_on_a_real_database_flags_a_dead_workers_job(isolated_dirs, quiet_disk):
    from jobs import queue
    job = queue.enqueue("render", "p-1")
    queue.claim("worker-gone")
    with db.get_engine().begin() as c:
        c.execute(db.jobs.update().where(db.jobs.c.id == job.id).values(
            heartbeat_at=monitoring.utcnow() - timedelta(minutes=10)))
    results = monitoring.evaluate(T)
    assert results["database"] is None
    assert problems(results) == {"job-silent"}
    assert results["disk"] is None and results["api-errors"] is None


def test_when_the_database_is_down_only_that_is_reported_and_no_secret_leaks(
        isolated_dirs, quiet_disk, monkeypatch):
    def down():
        raise RuntimeError("could not connect to postgresql://storygen:hunter2@db:5432/storygen")
    monkeypatch.setattr(monitoring, "check_database", down)
    results = monitoring.evaluate(T)
    assert "RuntimeError" in results["database"] and "hunter2" not in results["database"]
    # The job checks were not run, so they are absent: a missing fact is never "all well".
    for check in ("job-silent", "job-slow", "queue-waiting", "job-errors"):
        assert check not in results


def test_one_check_failing_to_run_does_not_stop_the_others(isolated_dirs, quiet_disk, monkeypatch):
    monkeypatch.setattr(monitoring, "read_requests", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    results = monitoring.evaluate(T)
    assert "API request log" in results["api-errors"]
    assert results["database"] is None and "disk" in results


# ---- telling the owner ---------------------------------------------------------------------

class Opener:
    """Stands in for urlopen: records what was sent and answers as told."""

    def __init__(self, status=200, error=None):
        self.status, self.error, self.sent = status, error, []

    def __call__(self, request, timeout=None):
        self.sent.append((request.full_url, request.get_method(), request.data, timeout))
        if self.error:
            raise self.error
        outer = self

        class Response:
            status = outer.status

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return Response()


BASE = "https://hc-ping.example/KEY-abc123"


def test_a_problem_goes_to_the_checks_fail_address_with_the_reason_as_the_body():
    opener = Opener()
    assert Pinger(BASE, opener=opener).send("disk", "The data disk is 85% full.")
    ((url, method, body, timeout),) = opener.sent
    assert url == f"{BASE}/dastango-disk/fail" and method == "POST"
    assert body == b"The data disk is 85% full." and timeout == 10.0


def test_all_well_goes_to_the_plain_address():
    opener = Opener()
    assert Pinger(BASE + "/", opener=opener).send("job-silent", None)
    assert opener.sent[0][0] == f"{BASE}/dastango-job-silent"


def test_a_long_reason_is_cut_to_a_thousand_bytes():
    opener = Opener()
    Pinger(BASE, opener=opener).send("job-errors", "x" * 5000)
    assert len(opener.sent[0][2]) == 1000


@pytest.mark.parametrize("opener", [Opener(status=500), Opener(error=OSError("no route")),
                                    Opener(error=TimeoutError(f"timed out reaching {BASE}"))])
def test_a_ping_that_fails_returns_false_and_the_log_never_holds_the_key(opener, caplog):
    with caplog.at_level(logging.DEBUG):
        assert Pinger(BASE, opener=opener).send("disk", "problem") is False
    assert "KEY-abc123" not in caplog.text and "hc-ping" not in caplog.text


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Recorder:
    def __init__(self, succeed=True):
        self.calls, self.succeed = [], succeed

    def send(self, check, message):
        self.calls.append((check, message))
        return self.succeed


def test_a_state_is_sent_once_then_only_when_it_changes_or_is_due_again():
    clock, pinger = Clock(), Recorder()
    reporter = Reporter(pinger, repeat_s=300, clock=clock)
    reporter.report({"disk": None})
    reporter.report({"disk": None})
    assert pinger.calls == [("disk", None)]             # a minute later: nothing new to say
    clock.now += 60
    reporter.report({"disk": "85% full"})
    reporter.report({"disk": "86% full"})               # still a problem: not repeated
    assert pinger.calls == [("disk", None), ("disk", "85% full")]
    clock.now += 60
    reporter.report({"disk": None})                     # recovered: the all-clear is sent
    assert pinger.calls[-1] == ("disk", None) and len(pinger.calls) == 3


def test_a_state_is_repeated_every_repeat_interval_so_the_service_knows_we_are_alive():
    clock, pinger = Clock(), Recorder()
    reporter = Reporter(pinger, repeat_s=300, clock=clock)
    reporter.report({"monitor": None})
    clock.now += 299
    reporter.report({"monitor": None})
    assert len(pinger.calls) == 1
    clock.now += 2
    reporter.report({"monitor": None})
    assert len(pinger.calls) == 2


def test_a_ping_that_did_not_get_through_is_tried_again_next_pass():
    clock, pinger = Clock(), Recorder(succeed=False)
    reporter = Reporter(pinger, repeat_s=300, clock=clock)
    reporter.report({"job-silent": "stuck"})
    clock.now += 60
    reporter.report({"job-silent": "stuck"})
    assert len(pinger.calls) == 2                       # not remembered as sent
    pinger.succeed = True
    clock.now += 60
    reporter.report({"job-silent": "stuck"})
    reporter.report({"job-silent": "stuck"})
    assert len(pinger.calls) == 3


def test_a_check_missing_from_the_results_is_never_pinged():
    pinger = Recorder()
    Reporter(pinger, 300, Clock()).report({"disk": None})
    assert [c for c, _ in pinger.calls] == ["disk"]


class OnePass:
    """A stop signal that lets the loop run exactly once."""

    def __init__(self):
        self.done = False

    def is_set(self):
        return self.done

    def wait(self, _timeout):
        self.done = True


def test_the_loop_reports_every_check_and_its_own_heartbeat(monkeypatch):
    monkeypatch.setattr(monitoring, "evaluate", lambda t: {"disk": "85% full", "api-errors": None})
    monkeypatch.setattr(monitoring, "mark_alive", lambda: None)
    pinger = Recorder()
    monitoring.run_forever(T, pinger, stop=OnePass())
    assert dict(pinger.calls) == {"disk": "85% full", "api-errors": None, "monitor": None}


def test_without_a_ping_address_the_loop_only_logs(monkeypatch, caplog):
    monkeypatch.setattr(monitoring, "evaluate", lambda t: {"disk": "85% full"})
    monkeypatch.setattr(monitoring, "mark_alive", lambda: None)
    with caplog.at_level(logging.INFO):
        monitoring.run_forever(T, None, stop=OnePass())
    assert "problem: disk: 85% full" in caplog.text and "only logged" in caplog.text


# ---- the command ------------------------------------------------------------------------------

def run_cli(monkeypatch, *argv, base=None):
    import main
    if base:
        monkeypatch.setenv("MONITOR_PING_BASE", base)
    args = main.build_parser().parse_args(["monitor", *argv])
    return main.cmd_monitor(args)


def test_monitor_is_a_command_not_a_film_prompt(monkeypatch):
    import main
    seen = []
    monkeypatch.setattr(main, "cmd_monitor", lambda args: seen.append(args.once) or 0)
    monkeypatch.setattr(sys, "argv", ["main.py", "monitor", "--once"])
    assert main.main() == 0 and seen == [True]


def test_once_prints_every_check_and_sends_nothing(isolated_dirs, monkeypatch, capsys):
    sent = Recorder()
    monkeypatch.setattr(Pinger, "send", lambda self, c, m: sent.send(c, m))
    assert run_cli(monkeypatch, "--once", base=BASE) == 0
    out = capsys.readouterr().out
    assert "database" in out and "disk" in out and sent.calls == []


def test_send_test_reports_a_problem_then_the_all_clear(monkeypatch, capsys):
    sent, slept = Recorder(), []
    monkeypatch.setattr(Pinger, "send", lambda self, c, m: sent.send(c, m))
    monkeypatch.setattr("time.sleep", slept.append)
    assert run_cli(monkeypatch, "--send-test", "job-silent", "--hold", "5", base=BASE) == 0
    assert [c for c, _ in sent.calls] == ["job-silent", "job-silent"]
    assert sent.calls[0][1] and sent.calls[1][1] is None and slept == [5.0]


def test_send_test_refuses_an_unknown_check_and_a_missing_address(monkeypatch, capsys):
    assert run_cli(monkeypatch, "--send-test", "nonsense", base=BASE) == 2
    monkeypatch.delenv("MONITOR_PING_BASE", raising=False)
    assert run_cli(monkeypatch, "--send-test", "disk") == 2


# ---- deployment: what the ticket asks the production stack to contain ----------------------

class _ComposeLoader(yaml.SafeLoader):
    """Compose's own `!reset` tag is not YAML the stock loader knows."""


_ComposeLoader.add_constructor("!reset", lambda loader, node: None)


def _compose(name):
    return yaml.load((ROOT / name).read_text(encoding="utf-8"), Loader=_ComposeLoader)


def test_the_production_image_installs_the_tracing_extra():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    sync = next(line for line in dockerfile.splitlines() if line.startswith("RUN uv sync"))
    assert "--extra tracing" in sync
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "\ntracing = [" in pyproject, "the tracing extra the image installs is gone"


def test_production_pins_the_environment_and_service_name_for_the_api_and_the_worker():
    services = _compose("deploy/docker-compose.prod.yml")["services"]
    for name in ("api", "worker"):
        env = services[name]["environment"]
        assert env["LANGFUSE_TRACING_ENVIRONMENT"] == "production", name
        assert env["OTEL_SERVICE_NAME"] == "dastango", name


def test_production_runs_a_monitor_with_the_data_volume_and_its_own_health_check():
    monitor = _compose("deploy/docker-compose.prod.yml")["services"]["monitor"]
    assert monitor["command"] == ["python", "main.py", "monitor"]
    assert monitor["restart"] == "unless-stopped"
    assert any(v.endswith(":/app/data") for v in monitor["volumes"])
    assert "storygen-monitor-alive" in monitor["healthcheck"]["test"][1]
    assert monitoring.ALIVE_FILE.name == "storygen-monitor-alive"


def test_no_alert_address_or_key_is_in_the_repository():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "MONITOR_PING_BASE=" in l)
    assert line.startswith("#") and line.split("=", 1)[1].strip() == ""


def test_every_check_is_explained_in_the_monitoring_guide():
    guide = (ROOT / "docs" / "MONITORING.md").read_text(encoding="utf-8")
    for check in monitoring.CHECKS:
        assert f"dastango-{check}" in guide, check
