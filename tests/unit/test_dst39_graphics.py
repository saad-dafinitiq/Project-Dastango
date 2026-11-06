"""DST-39 phase 3: titles over the film (title card, scene titles, name bars, end card).

The promises: a title never changes the film's length or sound (they are overlays), it keeps
clear of the subtitles and a phone's own buttons in every format, it is a version "Go back"
undoes, and a click and a typed sentence are the same operation.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from agents.edit_agent import EditAgent, ops
from agents.edit_agent.intent_classifier import classify
from agents.edit_agent.planner import plan
from agents.story_agent.planner import template_script
from shared import film_format, graphics
from shared.schemas.audio import AudioSegment, SceneTiming, TimingManifest
from shared.schemas.edit import EditCommand
from shared.schemas.story import GraphicsSpec
from tests.conftest import silence_tts

SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


def _film(fmt="16:9", style="realistic", spec=None, scene_ms=8000):
    script = template_script("g", "A robot learns to paint", target_duration_s=24, scene_count=3)
    script.story.format, script.story.style = fmt, style
    script.story.graphics = spec or GraphicsSpec(title_card=True, scene_titles=True, lower_thirds=True,
                                                 end_card=True, credits_text="Made with Dastango")
    slots = [SceneTiming(scene_id=s.scene_id, start_ms=i * scene_ms, end_ms=(i + 1) * scene_ms)
             for i, s in enumerate(script.scenes)]
    segs = []
    for i, sc in enumerate(script.scenes):
        for j, ln in enumerate(sc.dialogue):
            start = i * scene_ms + 600 + j * 2500
            segs.append(AudioSegment(segment_id=f"{sc.scene_id}_{j}", scene_id=sc.scene_id, line_id=ln.line_id,
                                     character_id=ln.character_id, file_path="x", kind="dialogue",
                                     start_ms=start, end_ms=start + 2000, duration_ms=2000, text=ln.text))
    manifest = TimingManifest(project_id="g", total_duration_ms=scene_ms * 3, segments=segs, scenes=slots)
    return script, manifest


def _events(ass):
    rows = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]

    def secs(ts):
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    out = []
    for r in rows:
        f = r.split(",", 9)
        out.append((secs(f[1]), secs(f[2]), f[9]))
    return out


def test_nothing_is_drawn_until_a_title_is_switched_on():
    script, manifest = _film(spec=GraphicsSpec())
    assert graphics.build_ass(script, manifest, 1920, 1080) is None


def test_each_title_appears_where_it_belongs_and_nothing_runs_past_the_film():
    script, manifest = _film()
    ass = graphics.build_ass(script, manifest, 1920, 1080)
    events = _events(ass)
    text = " ".join(e[2] for e in events)
    assert script.story.title.split()[0] in text                              # the title card
    assert "SCENE 2" in text and "SCENE 3" in text and "SCENE 1" not in text   # scene 1 has the title card
    assert "The End" in text and "Made with Dastango" in text
    assert all(0 <= s < e <= 24.01 for s, e, _ in events)                      # inside the timeline
    names = [c.name for c in script.characters.characters if c.role != "narrator"]
    spoken = {n for n in names if n in text}
    assert spoken, "a speaking character gets a name bar"
    assert all(text.count(n) == 1 for n in spoken)                            # once each, the first time
    narrator = [c.name for c in script.characters.characters if c.role == "narrator"]
    assert all(n not in text for n in narrator)


def test_titles_follow_the_films_speed_like_the_subtitles_do():
    script, manifest = _film()
    normal = [e for e in _events(graphics.build_ass(script, manifest, 1920, 1080)) if "SCENE 2" in e[2]][0]
    fast = [e for e in _events(graphics.build_ass(script, manifest, 1920, 1080, speed=2.0)) if "SCENE 2" in e[2]][0]
    assert fast[0] == pytest.approx((normal[0] - 0.25) / 2 + 0.25, abs=0.02)


@pytest.mark.parametrize("fmt", list(SIZES))
def test_name_bars_sit_above_the_subtitles_and_scene_titles_clear_the_top(fmt):
    w, h = SIZES[fmt]
    lay = graphics._layout(fmt, w, h)
    subs = film_format.subtitle_layout(fmt, w, h)
    two_lines_top = h - subs["margin_v"] - round(2.4 * subs["font_px"])
    assert h - lay["above_subs"] <= two_lines_top                             # clear of a two-line subtitle
    assert lay["top"] >= 0.07 * h and lay["margin_x"] >= 0.05 * w             # inside the safe area
    if fmt == "9:16":
        assert lay["top"] >= 0.11 * h                                         # under a phone's own header
        assert h - lay["above_subs"] <= h * 0.70                              # above the buttons zone


def test_the_styles_look_different_and_a_title_in_urdu_gets_a_face_with_its_letters():
    script, manifest = _film(style="realistic")
    anime_script, _ = _film(style="anime")
    real = graphics.build_ass(script, manifest, 1920, 1080)
    anime = graphics.build_ass(anime_script, manifest, 1920, 1080)
    assert real != anime and r"\fscx112" in anime and r"\fscx112" not in real
    script.story.graphics.title_text = "کہانی کا عنوان"
    urdu = graphics.build_ass(script, manifest, 1920, 1080, language="Urdu")
    face = [l for l in urdu.splitlines() if "کہانی" in l][0]
    assert graphics.TITLE_FONT not in face


def test_braces_and_backslashes_in_a_title_cannot_inject_ass_codes():
    script, manifest = _film()
    script.story.graphics.title_text = r"Hello {\fs1}\N world"
    ass = graphics.build_ass(script, manifest, 1920, 1080)
    line = [l for l in ass.splitlines() if "Hello" in l][0]
    assert r"{\fs1}" not in line and "Hello fs1N world" in line


def test_the_fonts_are_the_unmodified_interface_fonts_with_their_licences():
    for name in ("Chillax-Semibold.otf", "Synonym-Regular.otf", "Chillax-FFL.txt", "Synonym-FFL.txt"):
        assert (graphics.FONTS_DIR / name).stat().st_size > 10_000
    flt = graphics.filter_for(graphics.FONTS_DIR / "x.ass")
    assert "fontsdir" in flt and "assets/fonts" in flt


# ---- the operation, on a rendered film -----------------------------------------------------

def _frames(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def _frame_diff(a, b, at=0.6) -> float:
    """How different two films look at `at` seconds: mean absolute difference, 0 to 255."""
    def grab(p):
        return subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(at), "-i", str(p), "-frames:v", "1",
                               "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    x, y = grab(a), grab(b)
    return sum(abs(i - j) for i, j in zip(x, y)) / max(1, len(x))


def _agent(small_project, pid, **kw):
    state, sm = small_project(project_id=pid, **kw)
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    return state, sm, agent


@pytest.mark.parametrize("subtitles", [True, False])
def test_a_title_card_is_burned_in_without_changing_length_or_sound(small_project, subtitles, tmp_path):
    state, sm, agent = _agent(small_project, f"t_gfx_{subtitles}", with_subtitles=subtitles)
    import shutil
    before = tmp_path / "before.mp4"
    shutil.copy(state.video.final_video_path, before)       # the same file name is rewritten
    frames = _frames(before)
    master = state.audio.master_track
    done = agent.apply_op(state.project_id, "set_graphics", {"title_card": True})
    assert done.success, done.error
    after = sm.latest(state.project_id)
    assert _frames(after.video.final_video_path) == frames                     # the timeline is untouched
    assert after.audio.master_track == master
    assert _frame_diff(before, after.video.final_video_path, at=0.8) > 2.0       # something is drawn
    assert after.script.story.graphics.title_card is True


def test_titles_are_a_version_and_going_back_removes_them(small_project):
    state, sm, agent = _agent(small_project, "t_gver")
    done = agent.apply_op("t_gver", "set_graphics", {"end_card": True, "scene_titles": True})
    assert done.success and done.new_version == state.version + 1
    assert sm.history("t_gver")[-1]["edit_intent"]["query"] == "Titles: scene titles on, end card on"
    sm.revert("t_gver", state.version)
    assert sm.latest("t_gver").script.story.graphics.end_card is False


def test_titles_survive_later_edits_to_the_film(small_project):
    state, sm, agent = _agent(small_project, "t_gkeep")
    assert agent.apply_op("t_gkeep", "set_graphics", {"title_card": True, "title_text": "Dust"}).success
    assert agent.apply_op("t_gkeep", "set_transition", {"scene": "2", "transition": "wipe"}).success
    assert sm.latest("t_gkeep").script.story.graphics.title_text == "Dust"


def test_refusals_say_what_to_do_and_change_nothing(small_project):
    state, sm, agent = _agent(small_project, "t_gbad")
    for params, hint in (({}, "which titles"), ({"title_text": "Dust"}, "turn the title card on"),
                         ({"end_card": True, "credits_text": "x" * 200}, "")):
        with pytest.raises(ops.OpError, match=hint or "."):
            ops.check(state, "set_graphics", params)
    assert sm.latest("t_gbad").version == state.version
    state.script.story.graphics.title_card = True
    assert ops.check(state, "set_graphics", {"reset": True}) == "Titles: none"


SENTENCES = [
    ("add a title card",                          {"title_card": True}),
    ('add a title card that says "Dust"',         {"title_card": True, "title_text": "Dust"}),
    ("remove the scene titles",                   {"scene_titles": False}),
    ("show the character names",                  {"lower_thirds": True}),
    ("add an end card",                           {"end_card": True}),
    ("add scene titles and an end card",          {"scene_titles": True, "end_card": True}),
    ("no titles",                                 {"reset": True}),
]


@pytest.mark.parametrize("sentence,expected", SENTENCES)
def test_a_sentence_becomes_the_titles_operation(sentence, expected):
    got = classify(sentence, scenes=["scene_1", "scene_2"])
    assert got.intent == "set_graphics" and got.parameters == expected, got
    steps = plan(got)
    assert [s.name for s in steps] == ["set_graphics", "recompose_video"]


def test_a_click_and_a_sentence_give_the_same_titles(small_project):
    _a, sm_a, agent_a = _agent(small_project, "t_gclick")
    _b, sm_b, agent_b = _agent(small_project, "t_gwords")
    assert agent_a.apply_op("t_gclick", "set_graphics", {"scene_titles": True, "end_card": True}).success
    typed = agent_b.edit(EditCommand(project_id="t_gwords", query="add scene titles and an end card"))
    assert typed.success, typed.error
    assert sm_a.latest("t_gclick").script.story.graphics == sm_b.latest("t_gwords").script.story.graphics
    assert _frames(sm_a.latest("t_gclick").video.final_video_path) == _frames(sm_b.latest("t_gwords").video.final_video_path)


@pytest.mark.parametrize("fmt", ["9:16", "1:1"])
def test_titles_render_in_every_format_at_the_films_size(small_project, fmt):
    state, sm, agent = _agent(small_project, f"t_gf_{fmt[0]}", format=fmt)
    frames = _frames(state.video.final_video_path)
    done = agent.apply_op(state.project_id, "set_graphics", {"title_card": True, "end_card": True, "lower_thirds": True})
    assert done.success, done.error
    out = sm.latest(state.project_id).video
    assert _frames(out.final_video_path) == frames
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(out.final_video_path)], capture_output=True, text=True).stdout.strip()
    assert probe == f"{out.width},{out.height}"
