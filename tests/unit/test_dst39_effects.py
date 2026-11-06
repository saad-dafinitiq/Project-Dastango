"""DST-39: finishing effects drawn with MoviePy (grain, vignette, glow, cinema bars, glitch).

They change pixels, never the number of frames, so the film stays exactly as long as its
timeline; only the scene that has one is cut again; and a click equals a sentence.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from agents.edit_agent import EditAgent, ops
from agents.edit_agent.intent_classifier import classify
from agents.edit_agent.planner import plan
from shared import finish
from shared.schemas.edit import EditCommand
from shared.timeline import ms_to_frame
from tests.conftest import silence_tts


def _frames(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def _grab(path, at=1.0) -> bytes:
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
                           "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout


def _diff(a, b, at=1.0) -> float:
    x, y = _grab(a, at), _grab(b, at)
    return sum(abs(i - j) for i, j in zip(x, y)) / max(1, len(x))


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=12:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)
    return p


@pytest.mark.parametrize("kind", list(finish.EFFECTS))
def test_every_effect_changes_the_picture_and_keeps_every_frame(clip, tmp_path, kind):
    out = finish.apply(clip, tmp_path / f"{kind}.mp4", kind, "strong", 12)
    assert _frames(out) == _frames(clip) == 36
    assert _diff(clip, out, at=1.66 if kind == "glitch" else 1.1) > 0.5, kind   # a glitch flickers in bursts


def test_the_same_effect_twice_is_the_same_picture_and_strength_matters(clip, tmp_path):
    a = finish.apply(clip, tmp_path / "a.mp4", "grain", "normal", 12)
    b = finish.apply(clip, tmp_path / "b.mp4", "grain", "normal", 12)
    soft = finish.apply(clip, tmp_path / "s.mp4", "grain", "soft", 12)
    strong = finish.apply(clip, tmp_path / "t.mp4", "grain", "strong", 12)
    assert _grab(a) == _grab(b)                                   # the grain is seeded by the frame
    assert _diff(clip, strong) > _diff(clip, soft)


def test_an_unknown_effect_is_refused(clip, tmp_path):
    with pytest.raises(ValueError, match="vignette"):
        finish.apply(clip, tmp_path / "x.mp4", "sparkle", "normal", 12)


def _agent(small_project, pid):
    state, sm = small_project(project_id=pid)
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    return state, sm, agent


def test_an_effect_on_one_scene_cuts_only_that_scene_again_and_keeps_the_length(small_project):
    state, sm, agent = _agent(small_project, "t_fx")
    before = {f.scene_id: f.clip_path for f in state.video.frames}
    stamps = {f.scene_id: f.plan_signature for f in state.video.frames}
    done = agent.apply_op("t_fx", "set_effect", {"scene": "2", "effect": "grain", "strength": "soft"})
    assert done.success, done.error
    s = sm.latest("t_fx")
    assert s.script.scenes[1].effect.kind == "grain" and s.script.scenes[0].effect is None
    assert _frames(s.video.final_video_path) == ms_to_frame(s.audio.manifest.total_duration_ms, s.video.fps)
    now = {f.scene_id: f.plan_signature for f in s.video.frames}
    assert now["scene_1"] == stamps["scene_1"] and now["scene_3"] == stamps["scene_3"]
    assert now["scene_2"] != stamps["scene_2"]                    # only the scene with the look was drawn again
    assert sm.history("t_fx")[-1]["edit_intent"]["query"] == "Scene 2: soft film grain"


def test_an_effect_is_a_version_and_going_back_removes_it(small_project):
    state, sm, agent = _agent(small_project, "t_fxv")
    assert agent.apply_op("t_fxv", "set_effect", {"effect": "letterbox"}).success
    assert all(sc.effect and sc.effect.kind == "letterbox" for sc in sm.latest("t_fxv").script.scenes)
    sm.revert("t_fxv", state.version)
    back = sm.latest("t_fxv")
    assert all(sc.effect is None for sc in back.script.scenes)
    assert _frames(back.video.final_video_path) == _frames(state.video.final_video_path)


def test_none_and_reset_remove_the_effect_and_bad_choices_say_what_to_pick(small_project):
    state, sm, agent = _agent(small_project, "t_fxn")
    ops.apply(state, "set_effect", {"effect": "glow"})
    assert ops.apply(state, "set_effect", {"scene": "1", "effect": "none"}) == "Scene 1: no effect"
    assert state.script.scenes[0].effect is None and state.script.scenes[1].effect.kind == "glow"
    for params, hint in (({"effect": "sparkle"}, "which effect"), ({"effect": "glow", "strength": "huge"}, "how strong"),
                         ({}, "which effect"), ({"scene": "9", "effect": "glow"}, "no scene")):
        with pytest.raises(ops.OpError, match=hint):
            ops.check(state, "set_effect", params)


SENTENCES = [
    ("add film grain to scene 2",      {"effect": "grain"}, "scene:scene_2"),
    ("make the whole film vignette",   {"effect": "vignette"}, "global"),
    ("add a strong glow",              {"effect": "glow", "strength": "strong"}, "global"),
    ("cinema bars",                    {"effect": "letterbox"}, "global"),
    ("add a glitch effect to scene 3", {"effect": "glitch"}, "scene:scene_3"),
    ("remove the effects",             {"effect": "none"}, "global"),
]


@pytest.mark.parametrize("sentence,expected,scope", SENTENCES)
def test_a_sentence_becomes_the_effect_operation(sentence, expected, scope):
    got = classify(sentence, scenes=["scene_1", "scene_2", "scene_3"])
    assert got.intent == "set_effect" and got.parameters == expected and got.scope == scope, got
    assert [s.name for s in plan(got)] == ["set_effect", "recompose_video"]


def test_a_look_that_is_a_filter_stays_a_filter():
    assert classify("make scene 2 darker", scenes=["scene_1", "scene_2"]).intent == "adjust_scene_aesthetic"


def test_a_click_and_a_sentence_give_the_same_film(small_project):
    _a, sm_a, agent_a = _agent(small_project, "t_fxc")
    _b, sm_b, agent_b = _agent(small_project, "t_fxw")
    assert agent_a.apply_op("t_fxc", "set_effect", {"scene": "2", "effect": "grain"}).success
    typed = agent_b.edit(EditCommand(project_id="t_fxw", query="add film grain to scene 2"))
    assert typed.success, typed.error
    a, b = sm_a.latest("t_fxc"), sm_b.latest("t_fxw")
    assert a.script.scenes[1].effect == b.script.scenes[1].effect
    assert _frames(a.video.final_video_path) == _frames(b.video.final_video_path)
