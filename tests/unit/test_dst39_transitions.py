"""DST-39 phase 1: transitions, and the shared editing operations they are the first of.

The two promises that matter: every transition keeps the film exactly as long as its
timeline (frame-exact sync), and a click and a typed sentence do the same thing.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agents.edit_agent import EditAgent, ops
from agents.edit_agent.intent_classifier import classify
from agents.edit_agent.planner import plan
from agents.video_agent import animator, coverage
from agents.video_agent import agent as video_agent
from agents.video_agent import VideoAgent
from shared import transitions, xfade
from shared.schemas.edit import EditCommand
from shared.schemas.story import ShotOverride, TransitionSpec
from shared.timeline import ms_to_frame, xfade_frames
from tests.conftest import signed_in_client, silence_tts


def _frames(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


# ---- the styles and the arithmetic ----------------------------------------------------------

def test_the_picker_offers_the_main_six_first_and_only_what_this_ffmpeg_can_do():
    offered = transitions.styles()
    assert [s.id for s in offered[:6]] == ["cut", "fade", "fadeblack", "wipe", "slide", "zoom"]
    have = transitions.available_xfades()
    assert all(s.xfade is None or s.xfade in have for s in offered)
    assert len({s.id for s in offered}) == len(offered)


def test_unknown_styles_speeds_and_lengths_are_refused_with_the_choices():
    with pytest.raises(ValueError, match="fade"):
        transitions.get("spin")
    with pytest.raises(ValueError, match="quick"):
        transitions.speed_ms("glacial")
    for bad in (0, 50, 5000):
        with pytest.raises(ValueError):
            transitions.speed_ms(ms=bad)
    assert transitions.speed_ms("slow") == 800 and transitions.speed_ms(ms=600) == 600
    assert transitions.speed_ms() is None


def test_defaults_are_cuts_inside_a_scene_and_a_soft_fade_between_scenes():
    assert transitions.resolve(None, between_scenes=False) == ("cut", 0)
    assert transitions.resolve(None, between_scenes=True) == ("fade", 400)
    # a film made before keeps what it was cut with
    assert transitions.resolve(None, False, legacy=True) == ("fade", 200)
    assert transitions.resolve(None, True, legacy=True) == ("fade", 400)
    # a chosen style without a length takes the normal one; a cut has none whatever it is told
    assert transitions.resolve(TransitionSpec(kind="wipe"), False) == ("wipe", 400)
    assert transitions.resolve(TransitionSpec(kind="cut", ms=900), True) == ("cut", 0)


def test_a_transition_never_swallows_the_shots_it_joins():
    assert transitions.overlap_frames("cut", 400, 24, 100, 100) == 0
    assert transitions.overlap_frames("fade", 400, 24, 100, 100) == 10
    assert transitions.overlap_frames("fade", 800, 24, 100, 100) == 19
    assert transitions.overlap_frames("fade", 1500, 24, 30, 100) == 15        # half the shorter shot
    assert transitions.overlap_frames("fade", 400, 24, 1, 100) == 0           # too short to share


def test_what_people_call_a_transition_is_understood():
    for words, kind in [("a crossfade", "fade"), ("dip to black", "fadeblack"), ("a hard cut", "cut"),
                        ("wipe", "wipe"), ("push", "slide"), ("zoom in", "zoom"), ("pixelate", "pixelize")]:
        assert transitions.from_words(words) == kind, words
    assert transitions.from_words("something else entirely") is None


# ---- the filter graph ----------------------------------------------------------------------

def test_the_graph_places_each_boundary_at_the_sum_of_the_earlier_shots_not_their_tails():
    fc, last = xfade.build_graph([2.5, 3.0, 2.0], [("fade", 0.5), (None, 0)])
    assert last == "v2"
    # shot 1 is 2.5 s including 0.5 s of overlap: the fade starts at 2.0 s, where shot 1 ends
    assert "xfade=transition=fade:duration=0.500000:offset=2.000000[v1]" in fc
    assert "[v1][2:v]concat=n=2:v=1:a=0[v2]" in fc


def test_the_graph_refuses_a_wrong_number_of_boundaries():
    with pytest.raises(ValueError):
        xfade.build_graph([1, 1, 1], [("fade", 0.2)])
    assert xfade.build_graph([2.0], [])[0] == "[0:v]null[v0]"


# ---- real joins: every style is frame exact ---------------------------------------------------

def _clip(path: Path, frames: int, colour: str, fps: int = 24) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={colour}:s=128x72:r={fps}", "-frames:v", str(frames), "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


@pytest.mark.parametrize("style", [s.id for s in transitions.styles()])
def test_every_style_joins_two_shots_without_changing_the_length(tmp_path, style):
    """Two 48-frame shots (the first rendered with its overlap as extra frames): the join is
    exactly 96 frames whichever style joins them."""
    n = transitions.overlap_frames(style, 400, 24, 48, 48)
    a = _clip(tmp_path / "a.mp4", 48 + n, "red")
    b = _clip(tmp_path / "b.mp4", 48, "blue")
    out = animator.assemble_scene(
        [a, b], tmp_path / "scene.mp4", durations_s=[(48 + n) / 24, 2.0],
        boundaries=[(transitions.xfade_name(style), n / 24)])
    assert _frames(out) == 96


def test_a_scene_of_mixed_cuts_and_transitions_is_exact(tmp_path):
    lengths, overlaps = [30, 36, 42], [10, 0]
    clips = [_clip(tmp_path / f"{i}.mp4", lengths[i] + (overlaps[i] if i < 2 else 0), c)
             for i, c in enumerate(("red", "green", "blue"))]
    out = animator.assemble_scene(
        clips, tmp_path / "s.mp4",
        durations_s=[(lengths[i] + (overlaps[i] if i < 2 else 0)) / 24 for i in range(3)],
        boundaries=[("fade", 10 / 24), (None, 0)])
    assert _frames(out) == sum(lengths)


def test_a_scene_of_only_cuts_is_joined_without_being_encoded_again(tmp_path, monkeypatch):
    clips = [_clip(tmp_path / f"{i}.mp4", 24, c) for i, c in enumerate(("red", "green"))]
    ran = []
    real = subprocess.run
    monkeypatch.setattr(animator.subprocess, "run", lambda cmd, **kw: (ran.append(cmd), real(cmd, **kw))[1])
    out = animator.assemble_scene(clips, tmp_path / "s.mp4", durations_s=[1.0, 1.0],
                                  boundaries=[(None, 0)])
    assert _frames(out) == 48
    assert "-c" in ran[0] and "copy" in ran[0] and "libx264" not in ran[0]


# ---- the film ------------------------------------------------------------------------------

def _shots(state):
    return [(f.scene_id, s) for f in state.video.frames for s in f.shots]


def test_a_new_film_cuts_inside_a_scene_and_fades_between_scenes(small_project):
    state, _sm = small_project(project_id="t_def")
    assert state.video.legacy_transitions is False
    for f in state.video.frames:
        for s in f.shots[:-1]:
            assert (s.out_kind, s.out_frames) == ("cut", 0)
    scene_ends = [f.shots[-1] for f in state.video.frames[:-1]]
    assert all(s.out_kind == "fade" and s.out_frames > 0 for s in scene_ends)
    last = state.video.frames[-1].shots[-1]
    assert (last.out_kind, last.out_frames) == ("cut", 0)
    # and the film is exactly as long as the timeline
    assert _frames(state.video.final_video_path) == ms_to_frame(
        state.audio.manifest.total_duration_ms, state.video.fps)


def test_every_choice_leaves_the_film_exactly_the_timelines_length(small_project):
    state, _sm = small_project(project_id="t_all")
    expected = ms_to_frame(state.audio.manifest.total_duration_ms, state.video.fps)
    agent = VideoAgent()
    scenes = state.script.scenes
    ops.apply(state, "set_transition", {"scene": "2", "transition": "wipe", "speed": "slow"})
    ops.apply(state, "set_transition", {"scene": "3", "transition": "fadeblack"})
    ops.apply(state, "set_transition", {"scene": "2", "shot": 2, "transition": "zoom"})
    ops.apply(state, "set_transition", {"scene": "1", "shot": 2, "transition": "fade", "ms": 600})
    agent.compose(state)
    assert _frames(state.video.final_video_path) == expected
    ops.apply(state, "set_transition", {"scene": "2", "transition": "cut"})
    ops.apply(state, "set_transition", {"scene": "3", "transition": "cut"})
    agent.compose(state)
    assert _frames(state.video.final_video_path) == expected
    assert len(scenes) == 3


def test_an_old_film_keeps_the_fades_it_was_cut_with_and_does_not_redraw_when_edited(small_project, monkeypatch):
    state, _sm = small_project(project_id="t_old")
    # Make it an old film: the transitions it was cut with, then compose to settle the cache.
    state.video.legacy_transitions = True
    agent = VideoAgent()
    agent.compose(state, force=True)
    inside = [s for f in state.video.frames for s in f.shots[:-1]]
    assert all((s.out_kind, s.out_frames) == ("fade", xfade_frames(200, state.video.fps)) for s in inside)
    rendered = []
    real = video_agent.render_shot
    monkeypatch.setattr(video_agent, "render_shot", lambda *a, **k: (rendered.append(1), real(*a, **k))[1])
    agent.compose(state)                                                         # nothing changed
    assert rendered == []


def _scenes_assembled(monkeypatch):
    seen = []
    real = video_agent.assemble_scene

    def assemble(shots, out_path, **kw):
        seen.append(out_path.stem)
        return real(shots, out_path, **kw)

    monkeypatch.setattr(video_agent, "assemble_scene", assemble)
    return seen


def test_a_change_re_renders_only_the_scenes_it_touches(small_project, monkeypatch):
    state, _sm = small_project(project_id="t_scope")
    agent = VideoAgent()
    seen = _scenes_assembled(monkeypatch)
    # Inside scene 2: only scene 2.
    ops.apply(state, "set_transition", {"scene": "2", "shot": 2, "transition": "wipe"})
    agent.compose(state)
    assert seen == ["scene_2"]
    # Into scene 3: scene 2's last shot changes length (its overlap), so scene 2, never scene 1 or 3.
    seen.clear()
    ops.apply(state, "set_transition", {"scene": "3", "transition": "fadeblack", "speed": "slow"})
    agent.compose(state)
    assert seen == ["scene_2"]
    # Composing again with nothing changed renders nothing.
    seen.clear()
    agent.compose(state)
    assert seen == []


# ---- the operations ------------------------------------------------------------------------

def test_an_operation_that_does_not_fit_the_film_says_why(small_project):
    state, _sm = small_project(project_id="t_bad")
    bad = [
        ({"scene": "9", "transition": "fade"}, "no scene"),
        ({"scene": "1", "transition": "fade"}, "first shot of the film"),
        ({"scene": "2", "shot": 9, "transition": "fade"}, "no shot 9"),
        ({"scene": "2", "transition": "spin"}, "unknown transition"),
        ({"scene": "2", "transition": "fade", "speed": "glacial"}, "unknown speed"),
        ({"scene": "2", "transition": "fade", "ms": 9000}, "ms"),
        ({"scene": "2"}, "which transition"),
    ]
    for params, fragment in bad:
        with pytest.raises(ops.OpError, match=fragment):
            ops.check(state, "set_transition", params)
    with pytest.raises(ops.OpError, match="unknown edit"):
        ops.get("explode")
    with pytest.raises(ops.OpError):
        ops.parse("set_transition", {"transition": "fade"})                     # no scene
    with pytest.raises(ops.OpError, match="shot"):
        ops.parse("set_transition", {"scene": "2", "shot": 0, "transition": "fade"})
    # nothing was changed by any of that
    assert all(s.cut_in is None and not s.shot_overrides for s in state.script.scenes)


def test_a_transition_and_a_camera_choice_on_one_shot_do_not_undo_each_other(small_project):
    state, _sm = small_project(project_id="t_both")
    scene = state.script.scenes[1]
    sid = coverage.shot_id_at(scene.scene_id, 1)
    ops.apply(state, "set_transition", {"scene": "2", "shot": 2, "transition": "wipe"})
    ops.apply(state, "set_camera", {"scene": "2", "shot_id": sid, "move": "tilt_up"})
    kept = scene.shot_overrides[sid]
    assert kept.move == "tilt_up" and kept.transition.kind == "wipe"
    ops.apply(state, "set_camera", {"scene": "2", "shot_id": sid, "reset": True})
    assert scene.shot_overrides[sid].transition.kind == "wipe" and scene.shot_overrides[sid].move is None
    ops.apply(state, "set_transition", {"scene": "2", "shot": 2, "reset": True})
    assert sid not in scene.shot_overrides                                        # nothing left to hold


def test_the_words_say_what_was_done(small_project):
    state, _sm = small_project(project_id="t_words")
    assert ops.apply(state, "set_transition", {"scene": "2", "transition": "fade", "speed": "slow"}) == \
        "Between scenes 1 and 2: slow soft fade"
    assert ops.apply(state, "set_transition", {"scene": "3", "shot": 2, "transition": "cut"}) == \
        "Scene 3, shot 2: cut"
    assert ops.apply(state, "set_transition", {"scene": "2", "transition": "wipe", "ms": 600}) == \
        "Between scenes 1 and 2: 0.6 s wipe"
    assert ops.apply(state, "set_transition", {"scene": "2", "reset": True}) == \
        "Between scenes 1 and 2: back to the default"


def test_the_operations_are_registered_as_tools(small_project):
    import mcp.tools                                           # registers them
    from mcp.tool_executor import ToolExecutor
    from mcp.tools.edit_tools import OP_NAMES
    assert set(OP_NAMES) == set(ops.OPS)                       # the static list cannot drift
    listed = ToolExecutor().list_tools()
    assert all(f"edit.{n}" in listed for n in ops.OPS)
    state, _sm = small_project(project_id="t_tool")
    done = ToolExecutor().execute("edit.set_transition", state=state, scene="2", transition="slide")
    assert done.success and state.script.scenes[1].cut_in.kind == "slide"
    refused = ToolExecutor().execute("edit.set_transition", state=state, scene="2", transition="spin")
    assert not refused.success and "unknown transition" in refused.error


# ---- a click and a sentence ----------------------------------------------------------------

@pytest.mark.parametrize("sentence,scene,shot,params", [
    ("use a slow fade between scenes 1 and 2", "scene_2", None, {"transition": "fade", "speed": "slow"}),
    ("Make a quick wipe between scene 2 and scene 3", "scene_3", None, {"transition": "wipe", "speed": "quick"}),
    ("no transition between scenes 2 and 3", "scene_3", None, {"transition": "cut"}),
    ("dip to black into scene 3", "scene_3", None, {"transition": "fadeblack"}),
    ("make the cut into shot 2 of scene 2 a zoom", "scene_2", 2, {"transition": "zoom"}),
    ("scene 3, shot 2: use a 0.6 s dissolve", "scene_3", 2, {"transition": "dissolve", "ms": 600}),
])
def test_a_sentence_is_understood_as_the_operation_a_click_would_send(sentence, scene, shot, params):
    intent = classify(sentence, scenes=["scene_1", "scene_2", "scene_3"])
    assert intent.intent == "set_transition" and intent.scope == f"scene:{scene}"
    assert intent.parameters.get("shot") == shot
    for k, v in params.items():
        assert intent.parameters[k] == v
    steps = plan(intent)
    assert [s.name for s in steps] == ["set_transition", "recompose_video"]
    assert steps[0].params["scene"] == scene


def test_a_cut_with_no_place_is_asked_about_not_guessed():
    intent = classify("use a wipe", scenes=["scene_1", "scene_2"])
    assert intent.intent != "set_transition"
    from shared.schemas.edit import EditIntent
    with pytest.raises(ValueError, match="which cut"):
        plan(EditIntent(intent="set_transition", target="video", scope="global",
                        parameters={"transition": "wipe"}))


def test_a_click_and_a_sentence_leave_the_same_film_script(small_project):
    """The same transition by the API's operation and by a typed sentence: the same stored choice,
    the same film, one new version each."""
    a, sm_a = small_project(project_id="t_click")
    b, sm_b = small_project(project_id="t_words2")
    agent_a, agent_b = EditAgent(sm_a), EditAgent(sm_b)
    silence_tts(agent_a.executor.audio.tools)
    silence_tts(agent_b.executor.audio.tools)

    clicked = agent_a.apply_op("t_click", "set_transition",
                               {"scene": "scene_2", "transition": "fade", "speed": "slow"})
    typed = agent_b.edit(EditCommand(project_id="t_words2",
                                     query="use a slow fade between scenes 1 and 2"))
    assert clicked.success and typed.success, (clicked.error, typed.error)
    sa, sb = sm_a.latest("t_click"), sm_b.latest("t_words2")
    assert sa.script.scenes[1].cut_in == sb.script.scenes[1].cut_in == TransitionSpec(kind="fade", ms=800)
    assert [(s.out_kind, s.out_frames) for _i, s in _shots(sa)] == [(s.out_kind, s.out_frames) for _i, s in _shots(sb)]
    assert _frames(sa.video.final_video_path) == _frames(sb.video.final_video_path)
    assert sa.version == a.version + 1 and sb.version == b.version + 1


def test_a_transition_is_a_version_and_going_back_undoes_it(small_project):
    state, sm = small_project(project_id="t_undo")
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    before = _frames(state.video.final_video_path)
    result = agent.apply_op("t_undo", "set_transition", {"scene": "2", "transition": "slide"})
    assert result.success and result.new_version == state.version + 1
    assert sm.latest("t_undo").script.scenes[1].cut_in.kind == "slide"
    rows = sm.history("t_undo")
    assert rows[-1]["edit_intent"]["query"] == "Between scenes 1 and 2: slide"
    sm.revert("t_undo", state.version)
    gone = sm.latest("t_undo")
    assert gone.script.scenes[1].cut_in is None
    assert gone.version == state.version + 2                                    # going back is a new version
    assert _frames(gone.video.final_video_path) == before


def test_a_failed_change_leaves_the_film_as_it_was(small_project, monkeypatch):
    state, sm = small_project(project_id="t_fail")
    agent = EditAgent(sm)

    def boom(*_a, **_k):
        raise RuntimeError("ffmpeg fell over")

    monkeypatch.setattr(agent.executor.video, "compose", boom)
    result = agent.apply_op("t_fail", "set_transition", {"scene": "2", "transition": "wipe"})
    assert not result.success and "fell over" in result.error
    assert sm.latest("t_fail").version == state.version
    assert sm.latest("t_fail").script.scenes[1].cut_in is None


# ---- the storyboard and the API ------------------------------------------------------------

@pytest.fixture
def api(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import edit as edit_routes
    from backend.routes import pipeline as pipeline_routes
    from backend.services import pipeline_service

    state, sm = small_project(project_id="t_api")
    monkeypatch.setattr(pipeline_routes, "sm", sm)
    monkeypatch.setattr(edit_routes, "sm", sm)
    orch = pipeline_service.orchestrator()
    monkeypatch.setattr(orch, "sm", sm)
    monkeypatch.setattr(orch.editor, "sm", sm)
    return state, sm, signed_in_client(app_module.app)


def test_the_catalogue_lists_the_operations_and_their_choices(api):
    _state, _sm, client = api
    body = client.get("/api/edit/ops").json()
    names = [o["name"] for o in body["ops"]]
    assert "set_transition" in names and "set_camera" in names
    options = next(o for o in body["ops"] if o["name"] == "set_transition")["options"]
    assert [s["id"] for s in options["styles"] if s["main"]] == ["cut", "fade", "fadeblack", "wipe", "slide", "zoom"]
    assert options["speeds"] == {"quick": 200, "normal": 400, "slow": 800}


def test_the_api_refuses_what_does_not_fit_and_queues_a_change_on_a_finished_film(api):
    _state, _sm, client = api
    url = "/api/edit/t_api/op"
    assert client.post(url, json={"op": "explode", "params": {}}).status_code == 400
    assert client.post(url, json={"op": "set_transition", "params": {"scene": "2"}}).status_code == 404 \
        or client.post(url, json={"op": "set_transition", "params": {"scene": "2"}}).status_code == 400
    assert client.post(url, json={"op": "set_transition", "params": {"scene": "9", "transition": "fade"}}).status_code == 404
    assert client.post(url, json={"op": "set_transition", "params": {"scene": "2", "transition": "spin"}}).status_code == 404
    queued = client.post(url, json={"op": "set_transition", "params": {"scene": "2", "transition": "wipe"}})
    assert queued.status_code == 200 and queued.json()["job_id"]                  # a film is cut again: a job
    assert client.post(url, json={"op": "set_transition", "params": {"scene": "2", "transition": "slide"}}).status_code == 409


def test_before_a_render_a_change_is_immediate_and_the_cards_show_it(isolated_dirs, monkeypatch):
    from agents.orchestrator import PipelineOrchestrator
    from backend import app as app_module
    from backend.routes import edit as edit_routes
    from backend.routes import pipeline as pipeline_routes
    from backend.services import pipeline_service

    orch = PipelineOrchestrator()
    orch.plan("A clockmaker in a flooded city", target_duration_s=24, scene_count=3, project_id="t_sb")
    monkeypatch.setattr(pipeline_routes, "sm", orch.sm)
    monkeypatch.setattr(edit_routes, "sm", orch.sm)
    monkeypatch.setattr(pipeline_service, "orchestrator", lambda: orch)
    client = signed_in_client(app_module.app)

    board = client.get("/api/pipeline/storyboard/t_sb").json()
    first = board["frames"][0]["shots"]
    assert first[0]["transition"] is None                                         # nothing before the film
    assert first[1]["transition"] == {"kind": "cut", "ms": 0, "chosen": False, "between": False}
    second_first = board["frames"][1]["shots"][0]["transition"]
    assert second_first == {"kind": "fade", "ms": 400, "chosen": False, "between": True}

    done = client.post("/api/edit/t_sb/op", json={"op": "set_transition",
                                                  "params": {"scene": "scene_2", "transition": "wipe", "speed": "slow"}})
    assert done.status_code == 200
    after = done.json()["frames"][1]["shots"][0]["transition"]
    assert after == {"kind": "wipe", "ms": 800, "chosen": True, "between": True}
    # it is a saved version, with the creator's words
    assert orch.sm.history("t_sb")[-1]["edit_intent"]["query"] == "Between scenes 1 and 2: slow wipe"
    # the first shot of the film has nothing to change
    assert client.post("/api/edit/t_sb/op", json={"op": "set_transition",
                                                  "params": {"scene": "scene_1", "transition": "fade"}}).status_code == 404


def test_a_long_film_at_24_fps_is_not_one_frame_longer_than_its_timeline(tmp_path):
    """Found live: scenes of 274, 280 and 155 frames joined by two 10-frame fades came out 690
    frames, one more than the 689 of the timeline, because xfade offsets are floating-point
    seconds. The join is now told how many frames to write."""
    from mcp.tool_executor import ToolExecutor
    fps, frames = 24, [274, 280, 155]
    clips = []
    for i, n in enumerate(frames):
        p = tmp_path / f"c{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=160x90:r={fps}",
                        "-frames:v", str(n), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)
        clips.append(str(p))
    res = ToolExecutor().execute("video.compose", clips=clips, out_path=str(tmp_path / "out.mp4"),
                                 durations_s=[n / fps for n in frames],
                                 boundaries=[("fade", 10 / fps), ("fade", 10 / fps)], quality="final")
    assert res.success, res.error
    assert _frames(res.data) == sum(frames) - 20 == 689
