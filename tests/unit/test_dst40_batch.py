"""DST-40: changes are stacked and applied together, as one version, with the film cut once."""
from __future__ import annotations

import json
import subprocess

import pytest

from agents.edit_agent import EditAgent, ops
from shared.timeline import ms_to_frame
from tests.conftest import signed_in_client, silence_tts

ITEMS = [
    {"op": "set_transition", "params": {"scene": "2", "transition": "wipe", "ms": 650}},
    {"op": "set_music", "params": {"mood": "epic"}},
    {"op": "set_effect", "params": {"scene": "3", "effect": "grain", "strength": "soft"}},
    {"op": "set_graphics", "params": {"end_card": True}},
]


def _frames(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def test_a_stack_checks_in_order_and_a_later_change_sees_an_earlier_one(small_project):
    state, _sm = small_project(project_id="t_b_check")
    labels = ops.check_many(state, ITEMS)
    assert len(labels) == 4 and labels[0].startswith("Between scenes 1 and 2")
    # nothing was changed by checking
    assert state.script.scenes[1].cut_in is None
    # the second change only makes sense after the first: removing scene 2 then changing it is refused
    with pytest.raises(ops.OpError, match="no scene"):
        ops.check_many(state, [{"op": "remove_scene", "params": {"scene": "3"}},
                               {"op": "set_effect", "params": {"scene": "3", "effect": "glow"}}])


def test_the_film_is_mixed_and_cut_once_for_the_whole_stack(small_project):
    state, _sm = small_project(project_id="t_b_steps")
    steps = [s for s, _sc, _p in ops.after_steps_many(state, ITEMS)]
    assert steps == ["remix_audio", "recompose_video"]                  # not once per change


def test_a_stack_is_one_version_and_going_back_undoes_all_of_it(small_project):
    state, sm = small_project(project_id="t_b_one")
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    done = agent.apply_ops("t_b_one", ITEMS)
    assert done.success, done.error
    assert done.new_version == state.version + 1                         # one version, not four
    s = sm.latest("t_b_one")
    assert s.script.scenes[1].cut_in.kind == "wipe" and s.script.scenes[1].cut_in.ms == 650
    assert s.script.scenes[2].effect.kind == "grain" and s.script.story.graphics.end_card
    assert s.script.scenes[0].music_mood == "epic"
    assert _frames(s.video.final_video_path) == ms_to_frame(s.audio.manifest.total_duration_ms, s.video.fps)
    assert sm.history("t_b_one")[-1]["edit_intent"]["query"].startswith("4 changes:")
    sm.revert("t_b_one", state.version)
    back = sm.latest("t_b_one")
    assert back.script.scenes[1].cut_in is None and back.script.scenes[2].effect is None
    assert not back.script.story.graphics.end_card


def test_one_change_that_cannot_be_made_stops_the_whole_stack_and_changes_nothing(small_project):
    state, sm = small_project(project_id="t_b_bad")
    agent = EditAgent(sm)
    bad = ITEMS + [{"op": "set_transition", "params": {"scene": "9", "transition": "fade"}}]
    done = agent.apply_ops("t_b_bad", bad)
    assert not done.success and "no scene" in done.error
    assert sm.latest("t_b_bad").version == state.version


def test_the_api_checks_a_stack_and_queues_one_job_for_it(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import edit as edit_routes
    from backend.routes import pipeline as pipeline_routes

    state, sm = small_project(project_id="t_b_api")
    monkeypatch.setattr(pipeline_routes, "sm", sm)
    monkeypatch.setattr(edit_routes, "sm", sm)
    client = signed_in_client(app_module.app)
    body = {"ops": ITEMS}
    checked = client.post("/api/edit/t_b_api/ops/check", json=body)
    assert checked.status_code == 200 and len(checked.json()["labels"]) == 4
    assert client.post("/api/edit/t_b_api/ops/check", json={"ops": [{"op": "explode", "params": {}}]}).status_code == 400
    assert client.post("/api/edit/t_b_api/ops/check",
                       json={"ops": [{"op": "set_transition", "params": {"scene": "9", "transition": "fade"}}]}).status_code == 404
    queued = client.post("/api/edit/t_b_api/ops", json=body)
    assert queued.status_code == 200 and queued.json()["job_id"]
    assert client.post("/api/edit/t_b_api/ops", json=body).status_code == 409      # busy until it runs
    assert client.post("/api/edit/t_b_api/ops", json={"ops": []}).status_code == 422
