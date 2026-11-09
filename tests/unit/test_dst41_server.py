"""DST-41 (QA findings M4 and M5): a hung model cannot hold a typed change for a minute, and a render
is refused while the film is being changed."""
from __future__ import annotations

import pytest

from tests.conftest import signed_in_client


def test_a_hung_model_cannot_hold_a_typed_change_for_a_minute():
    """M4: Gemini hung for about 60 s on an edit sentence before Groq answered in 0.5 s."""
    from shared import providers

    cfg = providers.load(force=True)
    waits = {s.provider: s.timeout_s for s in cfg.specs("edit_intent") if s.provider != "mock"}
    assert waits["gemini"] <= 10 and waits["groq"] <= 10
    assert all(w <= 30 for name, w in waits.items() if name != "ollama"), waits     # a local model may be slow to load


@pytest.fixture
def client_and_film(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import edit as edit_routes
    from backend.routes import pipeline as pipeline_routes

    state, sm = small_project(project_id="t_busy")
    monkeypatch.setattr(pipeline_routes, "sm", sm)
    monkeypatch.setattr(edit_routes, "sm", sm)
    return signed_in_client(app_module.app), state.project_id


BODY = {"with_subtitles": True}


def test_a_render_is_refused_while_an_operation_runs(client_and_film):
    client, pid = client_and_film
    op = client.post(f"/api/edit/{pid}/op", json={"op": "set_volume", "params": {"target": "music", "percent": 60}})
    assert op.status_code == 200 and op.json()["job_id"]
    refused = client.post(f"/api/pipeline/render/{pid}", json=BODY)
    assert refused.status_code == 409 and "worked on" in refused.json()["detail"]


def test_a_render_is_refused_while_a_stack_or_a_sentence_runs(client_and_film):
    client, pid = client_and_film
    assert client.post(f"/api/edit/{pid}/ops", json={"ops": [
        {"op": "set_volume", "params": {"target": "music", "percent": 60}}]}).status_code == 200
    assert client.post(f"/api/pipeline/render/{pid}", json=BODY).status_code == 409


def test_a_render_is_refused_while_another_render_waits(client_and_film):
    client, pid = client_and_film
    first = client.post(f"/api/pipeline/render/{pid}", json=BODY)
    assert first.status_code == 200
    assert client.post(f"/api/pipeline/render/{pid}", json=BODY).status_code == 409      # the double click


def test_a_render_is_still_accepted_when_nothing_else_is_running(client_and_film):
    client, pid = client_and_film
    assert client.post(f"/api/pipeline/render/{pid}", json=BODY).status_code == 200


def test_a_typed_sentence_still_queues_behind_the_stack_it_belongs_to(client_and_film):
    """The stack's bar applies the changes as one job and then each typed sentence after it, queued in
    that order (web/components/studio/Studio.tsx applyPending): the queue runs one job per film at a time."""
    client, pid = client_and_film
    assert client.post(f"/api/edit/{pid}/ops", json={"ops": [
        {"op": "set_volume", "params": {"target": "music", "percent": 60}}]}).status_code == 200
    assert client.post(f"/api/edit/{pid}", json={"query": "make the music quieter"}).status_code == 200


# ---- Stop says so: a running job that is asked to stop shows "Cancelling" -----------------------

@pytest.fixture
def running_job(isolated_dirs, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from jobs import queue
    from state_manager.state_manager import StateManager
    from state_manager.storage import VersionStore

    monkeypatch.setattr(pipeline_routes, "sm", StateManager(VersionStore(isolated_dirs / "state.db")))
    client = signed_in_client(app_module.app)
    res = client.post("/api/pipeline/run", json={"prompt": "A kite over a quiet town"}).json()
    return client, res, queue


def test_stopping_a_running_job_writes_a_cancelling_event_once(running_job):
    client, res, queue = running_job
    assert queue.claim("w1") is not None                                   # now it is running
    first = client.post(f"/api/jobs/{res['job_id']}/cancel").json()
    assert first["status"] == "cancelling"
    client.post(f"/api/jobs/{res['job_id']}/cancel")                       # a second click
    events = queue.events_since(res["job_id"])
    cancelling = [e for e in events if e["phase"] == "cancelling"]
    assert len(cancelling) == 1 and cancelling[0]["message"] == "Cancelling"
    # a page opened later replays the log, so it shows the same
    status = client.get(f"/api/pipeline/status/{res['project_id']}").json()
    assert any(e["phase"] == "cancelling" for e in status["recent_events"])


def test_stopping_a_queued_job_just_cancels_it(running_job):
    client, res, queue = running_job
    assert client.post(f"/api/jobs/{res['job_id']}/cancel").json()["status"] == "cancelled"
    assert [e for e in queue.events_since(res["job_id"]) if e["phase"] == "cancelling"] == []


def test_stopping_a_finished_job_changes_nothing(running_job):
    client, res, queue = running_job
    job = queue.claim("w1")
    queue.complete(job.id)
    assert client.post(f"/api/jobs/{res['job_id']}/cancel").json()["status"] == "succeeded"
    assert [e for e in queue.events_since(res["job_id"]) if e["phase"] == "cancelling"] == []
