"""DST-41 (QA finding H3): Stop must stop an edit.

Found on a real film: Stop pressed 8 s into a grain edit let it run 140 s, save version 14, and only
then end the job as `cancelled` (its events read "complete (version 14)", then "cancelled"). The cut was
one long step with nothing in it for a Stop to land on, and the last check came after the save.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.edit_agent import EditAgent
from shared import finish, progress_hook
from shared.schemas.edit import EditCommand
from tests.conftest import silence_tts


class Stop(BaseException):
    """Stands in for JobCancelled, which is a BaseException too."""


# ---- the hook ---------------------------------------------------------------------------------

def test_a_pulse_asks_the_job_if_it_was_cancelled_at_most_once_a_second(monkeypatch):
    calls = []
    token = progress_hook.listen(None, lambda: calls.append(1))
    try:
        monkeypatch.setattr(progress_hook, "_last_pulse", [0.0])
        for _ in range(200):
            progress_hook.pulse()
        assert len(calls) == 1                     # a tight loop does not hammer the database
        monkeypatch.setattr(progress_hook, "PULSE_EVERY_S", 0.0)
        progress_hook.pulse()
        assert len(calls) == 2
    finally:
        progress_hook.stop(token)


def test_a_cancel_raised_in_a_pulse_gets_out(monkeypatch):
    def check():
        raise Stop()

    monkeypatch.setattr(progress_hook, "_last_pulse", [0.0])
    token = progress_hook.listen(None, check)
    try:
        with pytest.raises(Stop):
            progress_hook.pulse()
    finally:
        progress_hook.stop(token)


def test_a_pulse_with_nobody_listening_does_nothing():
    progress_hook.pulse()                           # every test and the command line


def test_a_status_check_that_fails_never_fails_the_work(monkeypatch):
    def check():
        raise RuntimeError("database is away")

    monkeypatch.setattr(progress_hook, "_last_pulse", [0.0])
    token = progress_hook.listen(None, check)
    try:
        progress_hook.pulse()
    finally:
        progress_hook.stop(token)


def test_the_listener_is_put_back_after_the_job():
    token = progress_hook.listen(lambda *_: None, lambda: None)
    progress_hook.stop(token)
    assert progress_hook._sink.get() is None and progress_hook._check.get() is None


# ---- the worker gives the hook something to call ---------------------------------------------

def test_the_worker_event_sink_can_be_asked_about_a_cancel_without_an_event(monkeypatch):
    from jobs import worker

    written = []
    monkeypatch.setattr(worker.queue, "append_event", lambda *a, **k: written.append(k))
    monkeypatch.setattr(worker.queue, "heartbeat", lambda _id: True)
    beat = SimpleNamespace(cancelled=threading.Event())
    push = worker._event_sink(SimpleNamespace(id="j", project_id="p"), beat)

    push.check_cancelled()                           # not cancelled: nothing happens, nothing is written
    assert written == []
    beat.cancelled.set()
    with pytest.raises(worker.JobCancelled):
        push.check_cancelled()
    with pytest.raises(worker.JobCancelled):         # an ordinary event notices it too
        push(SimpleNamespace(phase="edit", status="step", message="x", progress=0.0, payload={}))


def test_a_cancel_the_database_knows_about_lands_too(monkeypatch):
    from jobs import worker

    monkeypatch.setattr(worker.queue, "heartbeat", lambda _id: False)       # the row says: stop
    push = worker._event_sink(SimpleNamespace(id="j", project_id="p"), SimpleNamespace(cancelled=threading.Event()))
    with pytest.raises(worker.JobCancelled):
        push.check_cancelled()


# ---- an effect pass lets a Stop land ----------------------------------------------------------

def _clip(path: Path, seconds: float = 3.0) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=duration={seconds}:size=160x90:rate=12", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return path


def test_a_look_drawn_frame_by_frame_stops_when_told_to(tmp_path, monkeypatch):
    """Glow and glitch are drawn in Python, a frame at a time (grain, vignette and bars are ffmpeg, a few
    seconds each)."""
    src = _clip(tmp_path / "in.mp4", seconds=6.0)
    checks = []

    def check():
        checks.append(1)
        if len(checks) >= 2:
            raise Stop()

    monkeypatch.setattr(progress_hook, "PULSE_EVERY_S", 0.0)
    monkeypatch.setattr(progress_hook, "_last_pulse", [0.0])
    token = progress_hook.listen(None, check)
    try:
        with pytest.raises(Stop):
            finish.apply(src, tmp_path / "out.mp4", "glow", "normal", 12)      # drawn in Python, frame by frame
    finally:
        progress_hook.stop(token)
    assert 2 <= len(checks) < 72                     # it stopped well before all 72 frames were drawn


# ---- a Stop at the very end saves nothing -----------------------------------------------------

def _stop_at_the_save(kind, info):
    if kind == "step" and info["name"] == "save":
        raise Stop()


@pytest.mark.parametrize("how", ["op", "ops", "sentence"])
def test_a_stop_after_the_last_step_saves_no_version_and_puts_the_files_back(small_project, how):
    state, sm = small_project(project_id=f"t_cancel_{how}", with_bgm=False)
    master = Path(state.audio.master_track)
    before = master.read_bytes()
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)

    with pytest.raises(Stop):
        if how == "op":
            agent.apply_op(state.project_id, "set_music", {"mood": "tense"}, on_progress=_stop_at_the_save)
        elif how == "ops":
            agent.apply_ops(state.project_id, [{"op": "set_music", "params": {"mood": "tense"}}],
                            on_progress=_stop_at_the_save)
        else:
            agent.edit(EditCommand(project_id=state.project_id, query="add tense background music"),
                       on_progress=_stop_at_the_save)

    assert sm.latest(state.project_id).version == state.version        # nothing was saved
    assert master.read_bytes() == before                               # and the files are back


def test_an_edit_that_is_not_stopped_still_ends_with_a_save_step_then_a_version(small_project):
    state, sm = small_project(project_id="t_cancel_ok", with_bgm=False)
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    seen = []
    done = agent.apply_op(state.project_id, "set_volume", {"target": "music", "percent": 60},
                          on_progress=lambda kind, info: seen.append((kind, info.get("name"))))
    assert done.success and done.new_version == state.version + 1
    assert seen[-1] == ("step", "save")                                # the last look comes before the save


# ---- while a finished film is changed, its progress is the edit's own --------------------------

def test_the_orchestrator_reports_a_looks_pass_in_words_and_lets_a_stop_land(small_project):
    from agents.orchestrator.workflow import PipelineOrchestrator

    state, sm = small_project(project_id="t_cancel_events", with_bgm=False)
    orch = PipelineOrchestrator(state_manager=sm)
    silence_tts(orch.editor.executor.audio.tools)

    events = []

    def emit(ev):
        events.append(ev)
    emit.check_cancelled = lambda: None
    orch.apply_op(state.project_id, "set_effect", {"scene": "2", "effect": "vignette"}, on_event=emit)
    words = [e.message for e in events if e.phase == "edit" and e.status == "step"]
    assert any(w.startswith("Drawing the look over the scenes") for w in words), words
    assert "Saving the new version" in words
    assert "cut" not in words and "looks" not in words                  # never the raw step names

    # and the same edit, stopped when the look is about to be drawn, changes nothing
    sm_version = sm.latest(state.project_id).version
    stopper = lambda ev: (_ for _ in ()).throw(Stop()) if ev.message.startswith("Drawing the look") else None   # noqa: E731
    stopper.check_cancelled = lambda: None
    with pytest.raises(Stop):
        orch.apply_op(state.project_id, "set_effect", {"scene": "3", "effect": "glow"}, on_event=stopper)
    assert sm.latest(state.project_id).version == sm_version


# ---- a batch that is stopped does not run on ---------------------------------------------------

def test_a_stop_in_one_job_of_a_batch_keeps_the_waiting_ones_from_starting():
    """A Stop pressed during the shots of a render was only heard after every queued shot had been drawn,
    because leaving the pool waited for them all (28 to 38 s after Stop on the QA film)."""
    import time
    from shared.utils.parallel import run_jobs

    started = []

    def job(i):
        started.append(i)
        if i == 0:
            raise Stop()
        time.sleep(0.3)
        return i

    t0 = time.monotonic()
    with pytest.raises(Stop):
        run_jobs([(job, (i,)) for i in range(12)], workers=2, label="shots")
    assert len(started) <= 4, started                  # the two running, a couple that began as they ended
    assert time.monotonic() - t0 < 1.5                 # not twelve jobs of 0.3 s on two workers (1.8 s)


def test_a_batch_that_finishes_still_returns_every_result_in_order():
    from shared.utils.parallel import run_jobs
    assert run_jobs([(lambda i: i * i, (i,)) for i in range(7)], workers=3) == [0, 1, 4, 9, 16, 25, 36]
