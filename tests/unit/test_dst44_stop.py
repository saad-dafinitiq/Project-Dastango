"""DST-44 (item 1): Stop reaches an ffmpeg that is already running.

A cancel used to land only between steps, so a shot or a look in progress ran to its end first: about
21 s on the QA film. A command a job runs is now watched, and killed when the job is told to stop.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from shared import procs, progress_hook


class Stop(BaseException):
    """Stands in for JobCancelled, which is a BaseException too."""


SLOW = [sys.executable, "-c", "import time; time.sleep(60)"]


def _slow_ffmpeg(out: Path, seconds: int = 600) -> list:
    """A real ffmpeg that would run for a long time: a long test pattern through heavy filters, one thread
    (so a batch of them does not starve the machine)."""
    return ["ffmpeg", "-y", "-loglevel", "error", "-threads", "1", "-f", "lavfi",
            "-i", f"testsrc2=s=1280x720:r=60:d={seconds}", "-vf", "gblur=sigma=20,noise=alls=40:allf=t",
            "-c:v", "libx264", "-preset", "veryslow", "-threads", "1", str(out)]


def _following(flag: threading.Event, raises=True):
    def check():
        if flag.is_set():
            raise Stop()
    return progress_hook.listen(None, check if raises else None, flag.is_set)


# ---- nobody following: as it was -------------------------------------------------------------------

def test_with_nobody_following_it_is_subprocess_run():
    done = procs.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert done.stdout.strip() == "hi" and done.returncode == 0
    with pytest.raises(subprocess.CalledProcessError):
        procs.run([sys.executable, "-c", "raise SystemExit(3)"], check=True, capture_output=True)
    with pytest.raises(subprocess.TimeoutExpired):
        procs.run(SLOW, timeout=0.3)


# ---- following a job that is not stopped: the same answers -----------------------------------------

def test_a_command_that_is_not_stopped_gives_the_answers_subprocess_run_gives():
    flag = threading.Event()
    token = _following(flag)
    try:
        done = procs.run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
                         capture_output=True, text=True)
        assert (done.stdout.strip(), done.stderr.strip(), done.returncode) == ("out", "err", 0)
        with pytest.raises(subprocess.CalledProcessError) as bad:
            procs.run([sys.executable, "-c", "import sys; print('boom', file=sys.stderr); raise SystemExit(2)"],
                      check=True, capture_output=True)
        assert bad.value.returncode == 2 and b"boom" in bad.value.stderr
        with pytest.raises(subprocess.TimeoutExpired):
            procs.run(SLOW, timeout=0.5)
        echoed = procs.run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                           input="said", capture_output=True, text=True)
        assert echoed.stdout.strip() == "SAID"
    finally:
        progress_hook.stop(token)


# ---- stopped ---------------------------------------------------------------------------------------

def _stop_after(flag: threading.Event, seconds: float) -> threading.Thread:
    t = threading.Thread(target=lambda: (time.sleep(seconds), flag.set()))
    t.start()
    return t


def test_a_running_command_is_killed_within_a_second_of_the_stop():
    flag = threading.Event()
    token = _following(flag)
    stopper = _stop_after(flag, 0.5)
    t0 = time.monotonic()
    try:
        with pytest.raises(Stop):
            procs.run(SLOW, capture_output=True)
    finally:
        progress_hook.stop(token)
        stopper.join()
    assert time.monotonic() - t0 < 2.0, "it waited for the command to finish"


def test_a_real_ffmpeg_that_would_run_for_minutes_is_stopped_at_once(tmp_path):
    out = tmp_path / "shot.mp4"
    flag = threading.Event()
    token = _following(flag)
    stopper = _stop_after(flag, 1.5)
    t0 = time.monotonic()
    try:
        with pytest.raises(Stop):
            procs.run(_slow_ffmpeg(out), check=True, capture_output=True, timeout=600)
    finally:
        progress_hook.stop(token)
        stopper.join()
    assert time.monotonic() - t0 < 4.0
    assert not out.exists(), "half a shot was left behind"


def test_a_file_that_was_there_before_is_left_for_the_edit_to_put_back(tmp_path):
    """ffmpeg overwrote it when it started; putting it back is the saved version's restore, not this."""
    out = tmp_path / "shot.mp4"
    out.write_bytes(b"the previous shot")
    flag = threading.Event()
    token = _following(flag)
    stopper = _stop_after(flag, 1.0)
    try:
        with pytest.raises(Stop):
            procs.run(_slow_ffmpeg(out), check=True, capture_output=True)
    finally:
        progress_hook.stop(token)
        stopper.join()
    assert out.exists()          # not removed here: the edit puts the saved version's files back (next test)


def test_the_killed_process_is_really_gone(tmp_path):
    marker = tmp_path / "alive.txt"
    code = f"import time, pathlib\nwhile True:\n    pathlib.Path(r'{marker}').write_text(str(time.time()))\n    time.sleep(0.1)"
    flag = threading.Event()
    token = _following(flag)
    stopper = _stop_after(flag, 1.0)
    try:
        with pytest.raises(Stop):
            procs.run([sys.executable, "-c", code], capture_output=True)
    finally:
        progress_hook.stop(token)
        stopper.join()
    stamp = marker.read_text()
    time.sleep(0.6)
    assert marker.read_text() == stamp, "it was still writing after the stop"


def test_a_stop_noticed_by_the_flag_alone_still_stops_with_a_clear_error():
    """A job that offers only the cheap flag and no check: the command still dies, and the cancel is not lost."""
    flag = threading.Event()
    token = progress_hook.listen(None, None, flag.is_set)
    stopper = _stop_after(flag, 0.4)
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            procs.run(SLOW, capture_output=True)
    finally:
        progress_hook.stop(token)
        stopper.join()


def test_a_shot_thread_in_a_batch_is_stopped_with_the_job(tmp_path):
    """The shots of a render run in threads that carry the job's context: every one of them is killed."""
    from shared.utils.parallel import run_jobs

    flag = threading.Event()
    token = _following(flag)
    stopper = _stop_after(flag, 1.0)

    def shot(i):
        procs.run(_slow_ffmpeg(tmp_path / f"s{i}.mp4"), check=True, capture_output=True)

    t0 = time.monotonic()
    try:
        with pytest.raises(Stop):
            run_jobs([(shot, (i,)) for i in range(4)], workers=4, label="shots")
    finally:
        progress_hook.stop(token)
        stopper.join()
    assert time.monotonic() - t0 < 5.0
    assert not list(tmp_path.glob("*.mp4"))


# ---- the worker hears a Stop within a second -------------------------------------------------------

def test_the_worker_learns_of_a_stop_in_about_a_second_not_a_heartbeat_later(isolated_dirs):
    import jobs as job_queue
    from jobs import worker

    job = job_queue.enqueue("edit", "p_stop", {})
    claimed = job_queue.claim("w1")
    assert claimed and claimed.id == job.id
    with worker._Heartbeat(job.id) as beat:                      # the real interval: 15 s
        time.sleep(0.2)
        assert not beat.cancelled.is_set()
        job_queue.cancel(job.id)
        t0 = time.monotonic()
        assert beat.cancelled.wait(timeout=3.0), "the worker did not notice the Stop"
        assert time.monotonic() - t0 < worker.CANCEL_POLL_S + 0.8


def test_the_event_sink_offers_the_cheap_flag(isolated_dirs):
    import jobs as job_queue
    from jobs import worker

    job = job_queue.enqueue("edit", "p_stop2", {})
    claimed = job_queue.claim("w1")
    with worker._Heartbeat(claimed.id) as beat:
        push = worker._event_sink(claimed, beat)
        assert push.peek_cancelled() is False
        beat.cancelled.set()
        assert push.peek_cancelled() is True


def test_a_job_whose_ffmpeg_is_running_ends_as_cancelled_a_moment_after_the_stop(isolated_dirs):
    """The whole path: a job is running a long ffmpeg, someone presses Stop, the job ends as cancelled."""
    import jobs as job_queue
    from jobs import worker

    class SlowOrchestrator:
        def edit(self, project_id, query, on_event=None, user_id=None):
            from agents.orchestrator.workflow import _following_steps
            with _following_steps(on_event, project_id, editing=True):
                procs.run(SLOW, capture_output=True)

    job = job_queue.enqueue("edit", "p_stop3", {"query": "slow"})
    claimed = job_queue.claim("w1")
    result = {}

    def run():
        result["status"] = worker.run_job(claimed, orchestrator=SlowOrchestrator())

    t = threading.Thread(target=run)
    t.start()
    time.sleep(1.0)
    job_queue.cancel(job.id)
    t0 = time.monotonic()
    t.join(timeout=10)
    assert not t.is_alive(), "the job did not end"
    assert result["status"] == "cancelled" and time.monotonic() - t0 < 4.0
    assert job_queue.get(job.id).status == "cancelled"


def test_an_edit_stopped_while_ffmpeg_runs_saves_nothing_and_leaves_the_film_as_it_was(small_project):
    """The whole edit, on a real film: Stop arrives while it is being cut again; no version is saved and the
    files are the saved version's (a half-written shot is put back by the restore)."""
    from agents.edit_agent import EditAgent
    from tests.conftest import silence_tts

    state, sm = small_project(project_id="t_stop_mid", with_bgm=False)
    film = Path(state.video.final_video_path)
    before = film.read_bytes()
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    flag = threading.Event()
    token = _following(flag)
    stopper = _stop_after(flag, 0.5)
    try:
        with pytest.raises(Stop):
            agent.apply_op(state.project_id, "set_effect", {"effect": "grain", "strength": "strong"})
    finally:
        progress_hook.stop(token)
        stopper.join()
    assert sm.latest(state.project_id).version == state.version
    assert film.read_bytes() == before
