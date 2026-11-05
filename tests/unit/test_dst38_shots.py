"""DST-38 — the creator's own camera, progress in steps, and stand-in pictures.

Planning is tested directly (fast and exact); the film path goes through a real
(small) render, because an override that never reaches the picture proves nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.video_agent import camera, coverage
from agents.video_agent.coverage import CoverLine, plan_coverage
from shared import progress_hook, stand_in
from shared.schemas.story import ShotOverride
from shared.utils.parallel import run_jobs
from tests.conftest import signed_in_client


def L(who, start, end, narrator=False):
    return CoverLine(who, narrator, start, end, "Hello there.")


def _scene():
    return [L("n", 1500, 4000, narrator=True), L("A", 4250, 6500),
            L("B", 6750, 9000), L("A", 9250, 11000)]


def _plan(overrides=None, has_portrait=lambda _c: True):
    return plan_coverage(0, 11_600, _scene(), "calm", 12_000, 0, has_portrait=has_portrait,
                         scene_id="s1", overrides=overrides)


# ---- overrides on the plan ------------------------------------------------------------

def test_without_overrides_the_plan_is_what_it_was():
    assert _plan(None) == _plan({}) == plan_coverage(0, 11_600, _scene(), "calm", 12_000, 0)


def test_a_chosen_move_replaces_only_that_shots_move():
    base = _plan()
    changed = _plan({"s1_s2": ShotOverride(move="tilt_up")})
    assert changed[1].move == "tilt_up"
    assert [c.move for i, c in enumerate(changed) if i != 1] == \
           [c.move for i, c in enumerate(base) if i != 1]
    assert [(c.start_ms, c.end_ms) for c in changed] == [(c.start_ms, c.end_ms) for c in base]


def test_an_unknown_move_or_a_shot_that_is_not_there_is_ignored_not_misapplied():
    base = _plan()
    assert _plan({"s1_s2": {"move": "barrel_roll"}}) == base
    assert _plan({"s1_s9": ShotOverride(move="tilt_up")}) == base


def test_a_medium_can_be_made_wide_and_a_wide_medium_with_a_crop_of_the_same_picture():
    base = _plan()
    first_medium = next(i for i, c in enumerate(base) if c.framing == "medium")
    wide = _plan({coverage.shot_id_at("s1", first_medium): ShotOverride(framing="wide")})
    assert wide[first_medium].framing == "wide" and wide[first_medium].crop is None
    medium = _plan({"s1_est": ShotOverride(framing="medium")})
    assert medium[0].framing == "medium" and medium[0].source == "wide" and medium[0].crop


def test_a_close_up_needs_a_face_and_is_otherwise_left_alone():
    base = _plan()
    narr = next(i for i, c in enumerate(base) if not c.speakers and c.framing != "close")
    sid = coverage.shot_id_at("s1", narr)
    assert _plan({sid: ShotOverride(framing="close")})[narr].framing == base[narr].framing
    talking = next(i for i, c in enumerate(base) if c.framing == "medium" and c.speakers)
    made = _plan({coverage.shot_id_at("s1", talking): ShotOverride(framing="close")})[talking]
    assert made.framing == "close" and made.source == "portrait" and made.character_id
    nobody = _plan({coverage.shot_id_at("s1", talking): ShotOverride(framing="close")},
                   has_portrait=lambda _c: False)[talking]
    assert nobody.framing == "medium"


def test_every_move_the_options_offer_is_one_the_camera_can_make():
    from backend.routes.pipeline import shot_options
    assert {m["id"] for m in shot_options()["moves"]} == set(camera.MOVES)


# ---- steps ----------------------------------------------------------------------------

def test_run_jobs_reports_each_finished_job_to_the_listener_even_from_threads():
    seen = []
    token = progress_hook.listen(lambda step, done, total: seen.append((step, done, total)))
    try:
        run_jobs([(lambda i=i: i, ()) for i in range(5)], workers=3, step="shots")
    finally:
        progress_hook.stop(token)
    assert seen[0] == ("shots", 0, 5)
    assert sorted(d for _s, d, _t in seen) == [0, 1, 2, 3, 4, 5]
    assert {t for _s, _d, t in seen} == {5}


def test_with_no_listener_a_report_does_nothing_and_a_broken_listener_cannot_fail_a_job():
    progress_hook.report("shots", 1, 2)

    def broken(*_a):
        raise RuntimeError("the browser went away")

    token = progress_hook.listen(broken)
    try:
        assert run_jobs([(lambda: 7, ())], workers=1, step="voices") == [7]
    finally:
        progress_hook.stop(token)


# ---- stand-in pictures ----------------------------------------------------------------

def test_a_stand_in_marker_is_only_believed_while_the_picture_is_the_one_it_was_made_for(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(b"placeholder")
    assert not stand_in.is_stand_in(png)
    stand_in.mark(png, True)
    assert stand_in.is_stand_in(png)
    png.write_bytes(b"a real picture, a different size")      # drawn properly afterwards
    assert not stand_in.is_stand_in(png)
    stand_in.mark(png, True)
    stand_in.mark(png, False)
    assert not stand_in.is_stand_in(png)


# ---- the film path --------------------------------------------------------------------

@pytest.fixture
def rendered(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from backend.routes import projects as project_routes
    from backend.routes import history as history_routes
    from backend.services import pipeline_service

    state, sm = small_project(project_id="t_shots", subtitle_language="English")
    monkeypatch.setattr(pipeline_routes, "sm", sm)
    monkeypatch.setattr(project_routes, "sm", sm)
    monkeypatch.setattr(history_routes, "sm", sm)
    orch = pipeline_service.orchestrator()
    monkeypatch.setattr(orch, "sm", sm)
    monkeypatch.setattr(orch.editor, "sm", sm)
    return state, sm, orch, signed_in_client(app_module.app)


def test_a_shot_change_on_a_film_recuts_only_that_scene(rendered):
    state, sm, orch, _client = rendered
    sid = state.script.scenes[0].scene_id
    others = {f.scene_id: (f.clip_path, Path(f.clip_path).stat().st_mtime_ns)
              for f in state.video.frames if f.scene_id != sid}
    before = next(f for f in state.video.frames if f.scene_id == sid)
    shot = before.shots[0]
    new_move = "tilt_down" if shot.motion != "tilt_down" else "tilt_up"

    events = []
    orch.set_shot("t_shots", sid, shot.shot_id, move=new_move, on_event=events.append)

    after = sm.latest("t_shots")
    frame = next(f for f in after.video.frames if f.scene_id == sid)
    assert frame.shots[0].motion == new_move                       # it reached the picture
    scene = next(s for s in after.script.scenes if s.scene_id == sid)
    assert scene.shot_overrides[shot.shot_id].move == new_move     # and is remembered
    for f in after.video.frames:                                   # the other scenes were not redone
        if f.scene_id != sid:
            assert (f.clip_path, Path(f.clip_path).stat().st_mtime_ns) == others[f.scene_id]
    assert [e.phase for e in events][-1] == "complete"
    assert after.version == state.version + 1


def test_a_shot_that_is_not_there_changes_nothing(rendered):
    state, sm, orch, _client = rendered
    with pytest.raises(ValueError):
        orch.set_shot("t_shots", state.script.scenes[0].scene_id, "nope_s9", move="tilt_up")
    assert sm.latest("t_shots").version == state.version


def test_resetting_a_shot_puts_back_the_planned_camera(rendered):
    state, sm, orch, _client = rendered
    sid = state.script.scenes[0].scene_id
    shot = next(f for f in state.video.frames if f.scene_id == sid).shots[0]
    planned = shot.motion
    orch.set_shot("t_shots", sid, shot.shot_id, move="tilt_down" if planned != "tilt_down" else "tilt_up")
    orch.set_shot("t_shots", sid, shot.shot_id, reset=True)
    frame = next(f for f in sm.latest("t_shots").video.frames if f.scene_id == sid)
    assert frame.shots[0].motion == planned
    assert not next(s for s in sm.latest("t_shots").script.scenes
                    if s.scene_id == sid).shot_overrides


def test_the_api_refuses_an_empty_unknown_or_busy_request_and_queues_a_film_change(rendered):
    state, _sm, _orch, client = rendered
    sid = state.script.scenes[0].scene_id
    url = f"/api/pipeline/storyboard/t_shots/{sid}/shots/{sid}_est"
    assert client.patch(url, json={}).status_code == 400
    assert client.patch(url, json={"move": "barrel_roll"}).status_code == 400
    assert client.patch(f"/api/pipeline/storyboard/t_shots/{sid}/shots/{sid}_s9",
                        json={"move": "tilt_up"}).status_code == 404
    assert client.patch(f"/api/pipeline/storyboard/t_shots/nope/shots/nope_est",
                        json={"move": "tilt_up"}).status_code == 404
    queued = client.patch(url, json={"move": "tilt_up"})
    assert queued.status_code == 200 and queued.json()["job_id"]       # a film is cut again: a job
    assert client.patch(url, json={"move": "pan_left"}).status_code == 409   # one at a time


def test_the_film_and_its_versions_speak_the_creators_numbers(rendered):
    state, _sm, orch, client = rendered
    sid = state.script.scenes[0].scene_id
    film = client.get("/api/pipeline/film/t_shots").json()
    assert film["cut"] == 1 and film["stand_in_scenes"] == []
    orch.set_shot("t_shots", sid, f"{sid}_est", move="tilt_up")
    versions = client.get("/api/history/t_shots/film").json()
    assert [v["number"] for v in versions] == [1, 2]
    assert client.get("/api/pipeline/film/t_shots").json()["cut"] == 2
    assert versions[1]["label"].startswith("Scene 1:")


def test_a_stand_in_picture_is_reported_with_the_film(rendered):
    state, _sm, _orch, client = rendered
    frame = state.video.frames[1]
    stand_in.mark(frame.shot_bank[0], True)
    film = client.get("/api/pipeline/film/t_shots").json()
    assert film["stand_in_scenes"] == [frame.scene_id]


def test_the_cards_say_what_is_the_creators_own_and_whether_a_close_up_is_possible(rendered):
    from agents.orchestrator.storyboard_view import shot_cards

    state, _sm, _orch, _client = rendered
    scene = state.script.scenes[0].model_copy(deep=True)
    people = {c.id: c for c in state.script.characters.characters}
    plain = shot_cards(scene, people)
    assert plain[0]["overridden"] is False and plain[0]["move_id"] == plain[0]["move"].replace(" ", "_")
    scene.shot_overrides[f"{scene.scene_id}_est"] = ShotOverride(move="tilt_up")
    shots = shot_cards(scene, people)
    assert shots[0]["move_id"] == "tilt_up" and shots[0]["overridden"] is True
    assert all(isinstance(s["can_close"], bool) for s in shots)
