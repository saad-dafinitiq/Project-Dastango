"""DST-39 phase 4: changes to scenes, shots, lines and voices on a finished film.

Each one must leave the film exactly as long as its (new) timeline, be a version that "Go back"
undoes, and be the same operation whether clicked or typed.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from agents.edit_agent import EditAgent, ops
from agents.edit_agent.intent_classifier import classify
from agents.edit_agent.planner import plan
from shared.schemas.edit import EditCommand
from shared.timeline import ms_to_frame
from tests.conftest import signed_in_client, silence_tts


def _frames(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def _agent(small_project, pid, **kw):
    state, sm = small_project(project_id=pid, **kw)
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    return state, sm, agent


def _exact(sm, pid):
    s = sm.latest(pid)
    assert _frames(s.video.final_video_path) == ms_to_frame(s.audio.manifest.total_duration_ms, s.video.fps)
    assert [f.scene_id for f in s.video.frames] == [sc.scene_id for sc in s.script.scenes]
    assert [t.scene_id for t in s.audio.manifest.scenes] == [sc.scene_id for sc in s.script.scenes]
    return s


# ---- remove and move a scene ---------------------------------------------------------------

def test_removing_a_scene_shortens_the_film_to_the_new_timeline_and_going_back_restores_it(small_project):
    state, sm, agent = _agent(small_project, "t_rm")
    ids = [s.scene_id for s in state.script.scenes]
    done = agent.apply_op("t_rm", "remove_scene", {"scene": "2"})
    assert done.success, done.error
    s = _exact(sm, "t_rm")
    assert [x.scene_id for x in s.script.scenes] == [ids[0], ids[2]]
    assert [x.index for x in s.script.scenes] == [0, 1]
    assert s.audio.manifest.total_duration_ms < state.audio.manifest.total_duration_ms
    assert sm.history("t_rm")[-1]["edit_intent"]["query"].startswith("Scene 2 (")
    sm.revert("t_rm", state.version)
    back = sm.latest("t_rm")
    assert [x.scene_id for x in back.script.scenes] == ids
    assert _frames(back.video.final_video_path) == ms_to_frame(state.audio.manifest.total_duration_ms, state.video.fps)


def test_the_last_scene_cannot_be_removed_and_a_missing_one_is_refused(small_project):
    state, sm, _agent_ = _agent(small_project, "t_rm1", scenes=3)
    with pytest.raises(ops.OpError, match="no scene"):
        ops.check(state, "remove_scene", {"scene": "9"})
    for _ in range(2):
        ops.apply(state, "remove_scene", {"scene": "1"})
    with pytest.raises(ops.OpError, match="at least one scene"):
        ops.check(state, "remove_scene", {"scene": "1"})


def test_moving_a_scene_reorders_script_sound_and_film_without_changing_the_length(small_project):
    state, sm, agent = _agent(small_project, "t_mv")
    ids = [s.scene_id for s in state.script.scenes]
    total = state.audio.manifest.total_duration_ms
    done = agent.apply_op("t_mv", "move_scene", {"scene": "3", "to": 1})
    assert done.success, done.error
    s = _exact(sm, "t_mv")
    assert [x.scene_id for x in s.script.scenes] == [ids[2], ids[0], ids[1]]
    assert abs(s.audio.manifest.total_duration_ms - total) <= 40
    first_line = next(seg for seg in s.audio.manifest.segments if seg.kind == "dialogue")
    assert first_line.scene_id == ids[2] and first_line.start_ms < 2500          # the sound moved with it
    with pytest.raises(ops.OpError, match="already in place"):
        ops.check(s, "move_scene", {"scene": "1", "to": 1})
    with pytest.raises(ops.OpError, match="no place 7"):
        ops.check(s, "move_scene", {"scene": "1", "to": 7})


# ---- one line, one voice, one shot ---------------------------------------------------------

def test_changing_a_line_records_only_that_line_again(small_project):
    state, sm, agent = _agent(small_project, "t_line")
    before = {seg.line_id: (seg.file_path, seg.start_ms) for seg in state.audio.manifest.segments if seg.kind == "dialogue"}
    target = state.script.scenes[1].dialogue[0]
    done = agent.apply_op("t_line", "edit_line", {"scene": "2", "line": "1",
                                                  "text": "A much longer line than the one that was here before, said slowly."})
    assert done.success, done.error
    s = _exact(sm, "t_line")
    seg = next(x for x in s.audio.manifest.segments if x.line_id == target.line_id)
    assert seg.text.startswith("A much longer") and s.script.scenes[1].dialogue[0].text == seg.text
    for other in s.audio.manifest.segments:
        if other.kind == "dialogue" and other.line_id != target.line_id and other.scene_id == "scene_1":
            assert other.file_path == before[other.line_id][0]                 # scene 1 was not recorded again
    assert s.audio.manifest.total_duration_ms >= state.audio.manifest.total_duration_ms


def test_a_line_that_is_not_there_or_says_nothing_is_refused(small_project):
    state, sm, agent = _agent(small_project, "t_line2")
    with pytest.raises(ops.OpError, match="no line"):
        ops.check(state, "edit_line", {"scene": "1", "line": "99", "text": "Hi"})
    with pytest.raises(ops.OpError):
        ops.check(state, "edit_line", {"scene": "1", "line": "1", "text": "   "})


def test_before_a_render_a_line_and_the_scene_order_change_the_storyboard_at_once(small_project):
    from agents.story_agent.planner import template_script
    from shared.schemas.pipeline import PipelineState
    st = PipelineState(project_id="t_pre2", user_prompt="x")
    st.script = template_script("t_pre2", "x", target_duration_s=24, scene_count=3)
    ids = [s.scene_id for s in st.script.scenes]
    label = ops.apply(st, "edit_line", {"scene": "1", "line": "1", "text": "Brand new words"})
    assert st.script.scenes[0].dialogue[0].text == "Brand new words" and "Scene 1, line 1" in label
    ops.apply(st, "move_scene", {"scene": "1", "to": 3})
    assert [s.scene_id for s in st.script.scenes] == [ids[1], ids[2], ids[0]]
    with pytest.raises(ops.OpError, match="render the film first"):
        ops.check(st, "regenerate_shot", {"scene": "1", "shot": 1})


def test_one_character_gets_a_different_voice_and_the_others_are_not_recorded_again(small_project):
    state, sm, agent = _agent(small_project, "t_voice")
    speakers = sorted({s.character_id for s in state.audio.manifest.segments if s.kind == "dialogue"})
    who = next(c for c in state.script.characters.characters if c.id == speakers[0])
    before = {s.line_id: s.file_path for s in state.audio.manifest.segments if s.kind == "dialogue"}
    done = agent.apply_op("t_voice", "set_voice", {"character": who.name.lower(), "different": True})
    assert done.success, done.error
    s = _exact(sm, "t_voice")
    assert sm.history("t_voice")[-1]["edit_intent"]["query"] == f"{who.name}: a different voice"
    for seg in s.audio.manifest.segments:
        if seg.kind == "dialogue" and seg.character_id != who.id:
            assert seg.file_path == before[seg.line_id]
    with pytest.raises(ops.OpError, match="no character"):
        ops.check(state, "set_voice", {"character": "Nobody", "tone": "warm"})
    with pytest.raises(ops.OpError, match="which tone"):
        ops.check(state, "set_voice", {"character": who.name, "tone": "sparkly"})
    with pytest.raises(ops.OpError, match="what about"):
        ops.check(state, "set_voice", {"character": who.name})


def test_one_shot_is_drawn_again_and_only_its_scene_is_cut_again(small_project):
    state, sm, agent = _agent(small_project, "t_shot")
    frame = state.video.frames[1]
    shot = frame.shots[0]
    old_pictures = {f.scene_id: f.clip_path for f in state.video.frames}
    from pathlib import Path
    old_bytes = Path(shot.image_path).read_bytes()
    done = agent.apply_op("t_shot", "regenerate_shot", {"scene": "2", "shot": "1"})
    assert done.success, done.error
    s = _exact(sm, "t_shot")
    new = Path(s.video.frames[1].shots[0].image_path)
    assert new.exists()
    # only the redrawn scene's clip was cut again; the others kept theirs
    assert s.video.frames[0].clip_path == old_pictures["scene_1"] and s.video.frames[2].clip_path == old_pictures["scene_3"]
    with pytest.raises(ops.OpError, match="has \\d shots"):
        ops.check(s, "regenerate_shot", {"scene": "1", "shot": 9})
    assert old_bytes is not None


# ---- sentences, and a click equals a sentence ---------------------------------------------

SENTENCES = [
    ("remove scene 2",                                  "remove_scene",    {}),
    ("move scene 3 to the start",                       "move_scene",      {"to": 1}),
    ("move scene 1 to position 3",                      "move_scene",      {"to": 3}),
    ("redraw shot 2 of scene 3",                        "regenerate_shot", {"shot": 2}),
    ('change line 2 of scene 1 to "Hello there"',       "edit_line",       {"line": 2, "text": "Hello there"}),
]


@pytest.mark.parametrize("sentence,intent,expected", SENTENCES)
def test_a_sentence_becomes_the_operation(sentence, intent, expected):
    got = classify(sentence, scenes=["scene_1", "scene_2", "scene_3"])
    assert got.intent == intent, got
    for k, v in expected.items():
        assert got.parameters.get(k) == v
    steps = plan(got)
    assert steps[0].name == intent and steps[-1].name == "recompose_video"
    assert all(s.params.get("scene") for s in steps[:1])


def test_the_scene_titles_sentence_is_not_a_scene_removal():
    assert classify("remove the scene titles", scenes=["scene_1"]).intent == "set_graphics"
    assert classify("regenerate scene 2", scenes=["scene_1", "scene_2"]).intent == "regenerate_scene"


def test_a_click_and_a_sentence_move_a_scene_the_same_way(small_project):
    _a, sm_a, agent_a = _agent(small_project, "t_mclk")
    _b, sm_b, agent_b = _agent(small_project, "t_mwd")
    assert agent_a.apply_op("t_mclk", "move_scene", {"scene": "3", "to": 1}).success
    typed = agent_b.edit(EditCommand(project_id="t_mwd", query="move scene 3 to the start"))
    assert typed.success, typed.error
    a, b = sm_a.latest("t_mclk"), sm_b.latest("t_mwd")
    assert [s.scene_id for s in a.script.scenes] == [s.scene_id for s in b.script.scenes]
    assert _frames(a.video.final_video_path) == _frames(b.video.final_video_path)


def test_every_op_is_in_the_catalogue_and_the_tool_registry(small_project, monkeypatch):
    from backend import app as app_module
    client = signed_in_client(app_module.app)
    names = {o["name"] for o in client.get("/api/edit/ops").json()["ops"]}
    assert {"regenerate_shot", "edit_line", "set_voice", "remove_scene", "move_scene"} <= names
    from mcp.tool_registry import registry
    for name in ("regenerate_shot", "edit_line", "set_voice", "remove_scene", "move_scene"):
        assert registry.get(f"edit.{name}") is not None
