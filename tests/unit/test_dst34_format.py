"""DST-34: the creator's format (16:9, 9:16, 1:1) and style (realistic, anime), and the
scene count, govern every agent.

The film is the unit: a format or style that holds on the storyboard and not in the
render, or in the render and not in an edit, or with the first image provider and not
the fallback, is a bug. So most of these go through a real (small) render.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from agents.story_agent import scenes as scene_gov
from agents.story_agent.agent import StoryAgent
from agents.story_agent.visual_style import portrait_style, scene_style, style_for
from agents.video_agent import camera, coverage
from agents.video_agent.coverage import CoverLine, plan_coverage
from mcp.tools.vision_tools import image_gen_tool
from shared import film_format, video_quality
from shared.schemas.pipeline import PipelineState
from shared.schemas.story import StoryOutput
from shared.timeline import ms_to_frame
from tests.conftest import signed_in_client

SMALL = {"16:9": (320, 180), "9:16": (180, 320), "1:1": (240, 240)}
FORMATS = list(SMALL)


def _story(**kw) -> StoryOutput:
    base = dict(project_id="p", title="t", logline="l", synopsis="s", genre="fantasy",
                visual_style="golden hour palette, soft light")
    return StoryOutput(**{**base, **kw})


# ---- the numbers --------------------------------------------------------------------

def test_each_format_has_its_own_film_and_picture_size():
    assert film_format.film_size("16:9") == (1920, 1080)
    assert film_format.film_size("9:16") == (1080, 1920)
    assert film_format.film_size("1:1") == (1080, 1080)
    # Pictures: long side 1280 (unchanged), a square at the providers' own 1024.
    assert video_quality.image_size(1920, 1080) == (1280, 720)
    assert video_quality.image_size(1080, 1920) == (720, 1280)
    assert video_quality.image_size(1080, 1080) == (1024, 1024)
    # Small test films keep their size.
    assert video_quality.image_size(320, 180) == (320, 180)


def test_unknown_choices_are_refused_with_what_is_accepted():
    for bad in ("4:3", "wide", ""):
        with pytest.raises(ValueError, match="16:9"):
            film_format.check_format(bad)
    with pytest.raises(ValueError, match="realistic"):
        film_format.check_style("watercolour")
    assert film_format.check_style(" Anime ") == "anime"
    assert film_format.format_of_size(1080, 1920) == "9:16"
    assert film_format.format_of_size(1920, 1080) == "16:9"
    assert film_format.format_of_size(1080, 1080) == "1:1"


def test_a_film_made_before_this_loads_as_it_was():
    story = StoryOutput.model_validate({"project_id": "p", "title": "t", "logline": "l", "synopsis": "s"})
    assert story.format == "16:9" and story.style == ""
    # And keeps the look its genre gave it: no lock, no composition sentence.
    style, _neg = style_for(_story(genre="horror", visual_style=""))
    assert "horror" in style and "photorealistic" not in style


# ---- subtitles ------------------------------------------------------------------------

def test_16x9_subtitles_are_what_they_always_were():
    """FontSize 22, MarginV 40 and MarginL/R 80 on libass's 384 x 288 grid, in pixels."""
    lay = film_format.subtitle_layout("16:9", 1920, 1080)
    assert lay["font_px"] == round(22 * 1080 / 288)
    assert lay["margin_v"] == round(40 * 1080 / 288)
    assert lay["margin_l"] == lay["margin_r"] == round(80 * 1920 / 384)


def test_vertical_subtitles_clear_the_phone_interface_and_stay_inside_the_sides():
    lay = film_format.subtitle_layout("9:16", 1080, 1920)
    assert lay["margin_v"] >= 0.2 * 1920                    # above the bottom fifth
    assert lay["margin_l"] + lay["margin_r"] <= 0.25 * 1080  # most of the width is text
    assert lay["font_px"] <= 0.045 * 1920


def test_burned_text_stays_inside_the_layout_in_every_format(tmp_path):
    """Burn a long line into a black frame of each shape and measure where the letters are."""
    from PIL import Image
    from mcp.tools.video_tools.subtitle_tool import SubtitleTool

    text = "Every story begins in stillness, and here is where ours starts to move."
    for fmt, (w, h) in {"16:9": (960, 540), "9:16": (540, 960), "1:1": (720, 720)}.items():
        src = tmp_path / f"{fmt.replace(':', 'x')}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"color=c=black:s={w}x{h}:d=2:r=12", "-pix_fmt", "yuv420p", str(src)], check=True)
        lay = film_format.subtitle_layout(fmt, w, h)
        out = tmp_path / f"{fmt.replace(':', 'x')}_sub.mp4"
        res = SubtitleTool().run(in_path=str(src), out_path=str(out),
                                 lines=[{"start_ms": 0, "end_ms": 2000, "text": text}],
                                 layout=lay, frame=(w, h))
        assert res.success, res.error
        png = tmp_path / f"{fmt.replace(':', 'x')}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(out),
                        "-frames:v", "1", str(png)], check=True)
        im = Image.open(png).convert("L")
        bright = im.point(lambda v: 255 if v > 200 else 0).getbbox()
        assert bright, f"no text drawn in {fmt}"
        left, top, right, bottom = bright
        # Letters sit above the bottom margin (a little slack for descenders and the outline)
        assert bottom <= h - lay["margin_v"] + lay["outline"] * 3, (fmt, bright)
        assert left >= lay["margin_l"] - lay["outline"] * 3, (fmt, bright)
        assert right <= w - lay["margin_r"] + lay["outline"] * 3, (fmt, bright)
        assert top > h * 0.5, (fmt, bright)                    # in the lower half, as subtitles are


# ---- the style lock --------------------------------------------------------------------

@pytest.mark.parametrize("style", ["realistic", "anime"])
def test_the_medium_comes_first_and_the_story_cannot_switch_it(style):
    other = "anime" if style == "realistic" else "realistic"
    story = _story(style=style, format="9:16",
                   visual_style=f"{film_format.STYLES[other]['label'].lower()}, painterly, "
                                "photographic, golden hour palette, volumetric light")
    for fn in (lambda: style_for(story), lambda: scene_style(story, "tense"), lambda: portrait_style(story)):
        positive, negative = fn()
        assert positive.startswith(film_format.STYLES[style]["medium"])
        assert "golden hour palette" in positive               # the palette survives
        assert "tall framing" in positive                      # the format's composition too
        # The clauses naming a medium the film is not in are gone from the story's own text.
        tail = positive[len(film_format.STYLES[style]["medium"]):]
        assert not re.search(film_format.STYLES[style]["forbidden"], tail, re.I), tail
        assert film_format.STYLES[style]["avoid"].split(",")[0] in negative


def test_no_style_keeps_the_genre_look_so_old_films_are_unchanged():
    legacy = _story(genre="horror", visual_style="", style="")
    assert style_for(legacy) == style_for(legacy.model_copy(update={"format": "9:16"}))


class _Recorder:
    """Every picture the film asks for: (style, negative, size)."""

    def __init__(self, monkeypatch):
        self.calls = []
        original = image_gen_tool.ImageGenTool.run
        calls = self.calls

        def run(tool, prompt, out_path, width=1280, height=720, seed=None, style="",
                negative_prompt="", **kw):
            calls.append({"style": style, "negative": negative_prompt, "size": (width, height),
                          "prompt": prompt})
            return original(tool, prompt, out_path, width=width, height=height, seed=seed,
                            style=style, negative_prompt=negative_prompt, **kw)

        monkeypatch.setattr(image_gen_tool.ImageGenTool, "run", run)


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("style", ["realistic", "anime"])
def test_every_image_path_follows_the_spec_including_edits(small_project, monkeypatch, fmt, style):
    """Portraits, shot banks, and a picture an edit draws again, all in the chosen look and shape."""
    from agents.edit_agent import EditAgent
    from shared.schemas.edit import EditCommand
    from tests.conftest import silence_tts

    rec = _Recorder(monkeypatch)
    state, sm = small_project(project_id=f"t_{fmt.replace(':', 'x')}_{style}", format=fmt, style=style)
    built = len(rec.calls)
    assert built >= 6                                          # 3 portraits + at least 3 scenes
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    result = agent.edit(EditCommand(project_id=state.project_id, query="regenerate scene 2"))
    assert result.success, result.error
    assert len(rec.calls) > built                              # the edit really drew again

    medium = film_format.STYLES[style]["medium"]
    for c in rec.calls:
        assert c["style"].startswith(medium), c["style"][:90]
        assert film_format.STYLES[style]["avoid"].split(",")[0] in c["negative"]
        assert film_format.composition(fmt) in c["style"]
        w, h = c["size"]
        assert (w > h, w == h) == (fmt == "16:9", fmt == "1:1"), (fmt, c["size"])


def test_the_fallback_provider_gets_the_same_look(monkeypatch, tmp_path):
    """The first provider failing must not switch the look: the prompt reaching the provider that
    finally answers is the one the first got."""
    from shared.providers import ProviderSpec

    seen = []

    def boom(self, spec, prompt, negative, out, w, h, seed):
        seen.append(("first", prompt, negative))
        raise RuntimeError("down")

    def second(self, spec, prompt, negative, out, w, h, seed):
        seen.append(("second", prompt, negative))
        out.write_bytes(b"x")
        return {}

    monkeypatch.setattr(image_gen_tool.ImageGenTool, "_provider_boom", boom, raising=False)
    monkeypatch.setattr(image_gen_tool.ImageGenTool, "_provider_second", second, raising=False)
    monkeypatch.setattr(image_gen_tool.providers, "chain",
                        lambda role: [ProviderSpec(role="image", provider="boom"),
                                      ProviderSpec(role="image", provider="second")])
    style, negative = style_for(_story(style="anime"))
    res = image_gen_tool.ImageGenTool().run("a boy", str(tmp_path / "a.png"), 180, 320,
                                            style=style, negative_prompt=negative)
    assert res.success and res.metadata["provider"] == "second" and res.metadata["fallback"]
    assert seen[0][1:] == seen[1][1:]
    assert seen[1][1].endswith(style)


# ---- the size reaches each provider ------------------------------------------------------

def test_the_frame_shape_reaches_every_provider_that_takes_one(monkeypatch, tmp_path):
    from PIL import Image
    import io

    tool = image_gen_tool.ImageGenTool()
    from shared.providers import ProviderSpec

    # Agnes: a ratio, not pixels.
    assert image_gen_tool._agnes_ratio(720, 1280) == "9:16"
    assert image_gen_tool._agnes_ratio(1280, 720) == "16:9"
    assert image_gen_tool._agnes_ratio(1024, 1024) == "1:1"

    # OpenAI: only three shapes exist, and a portrait film must not get the landscape one.
    sizes = []

    class FakeImages:
        def generate(self, **kw):
            sizes.append(kw["size"])
            raise RuntimeError("stop here")

    class FakeClient:
        def __init__(self, **_kw):
            self.images = FakeImages()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    for w, h in ((1280, 720), (720, 1280), (1024, 1024)):
        with pytest.raises(RuntimeError):
            tool._provider_openai(ProviderSpec(role="image", provider="openai"), "p", "", tmp_path / "x.png", w, h, 1)
    assert sizes == ["1792x1024", "1024x1792", "1024x1024"]

    # Pollinations: width and height are in the request.
    urls = []

    class Resp:
        status_code = 200
        headers = {}
        content = b""

        def raise_for_status(self):
            pass

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: (urls.append(url), (_ for _ in ()).throw(RuntimeError("stop")))[1])
    for w, h in ((1280, 720), (720, 1280)):
        with pytest.raises(RuntimeError):
            tool._provider_pollinations(ProviderSpec(role="image", provider="pollinations"), "p", "", tmp_path / "y.png", w, h, 1)
    assert any("width=720&height=1280" in u for u in urls) and any("width=1280&height=720" in u for u in urls)

    # A provider that only makes squares (FLUX on Cloudflare) is cover-fitted to the film's shape.
    square = io.BytesIO()
    Image.new("RGB", (1024, 1024), "red").save(square, "PNG")
    native = image_gen_tool._save_image_bytes(square.getvalue(), tmp_path / "z.png", 720, 1280)
    assert native == (1024, 1024) and Image.open(tmp_path / "z.png").size == (720, 1280)


# ---- the film: size, sync, camera --------------------------------------------------------

def _stream(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


def test_each_format_renders_at_its_own_size_with_sync_unchanged(small_project):
    counts = {}
    for fmt in FORMATS:
        state, _sm = small_project(project_id=f"t_sync_{fmt.replace(':', 'x')}", format=fmt)
        s = _stream(state.video.final_video_path)
        assert (s["width"], s["height"]) == SMALL[fmt]
        # The film is exactly as long as the timeline, whatever its shape.
        assert int(s["nb_read_frames"]) == ms_to_frame(state.audio.manifest.total_duration_ms, state.video.fps)
        counts[fmt] = int(s["nb_read_frames"])
    assert len(set(counts.values())) == 1


def test_a_tall_frame_tilts_and_alternates_its_medium_up_and_down():
    lines = [CoverLine("n", True, 1500, 4000, "x"), CoverLine("A", 4250, 6500, "x"),
             CoverLine("B", 6750, 9000, "x"), CoverLine("A", 9250, 11000, "x")]
    wide = plan_coverage(0, 11_600, lines, "calm", 12_000, 0, scene_id="s1")
    tall = plan_coverage(0, 11_600, lines, "calm", 12_000, 0, scene_id="s1", tall=True)
    assert wide[0].move == "pan_right" and tall[0].move in ("tilt_up", "tilt_down")
    assert [c.framing for c in wide] == [c.framing for c in tall]          # the same cuts
    assert [(c.start_ms, c.end_ms) for c in wide] == [(c.start_ms, c.end_ms) for c in tall]
    mediums = [c for c in tall if c.framing == "medium"]
    assert mediums and all(c.crop[1] == 0.5 for c in mediums)               # centre line
    assert len({c.crop[2] for c in coverage.plan_coverage(
        0, 30_000, [CoverLine("n", True, 1500, 3000, "x")] * 6, "calm", 30_000, 0, tall=True) if c.crop}) >= 1
    assert camera.move_for_role("establishing", scene_index=1, tall=True) == "tilt_down"
    assert camera.move_for_role("establishing", scene_index=1) == "pan_left"   # wide films as before


# ---- scene count --------------------------------------------------------------------------

def _script(scenes: int):
    from agents.story_agent.planner import template_script
    script = template_script("pid", "A lighthouse keeper befriends a stranded whale",
                             target_duration_s=30, scene_count=scenes)
    return script


class FakeModel:
    provider = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def generate_structured(self, prompt, schema, **_kw):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer.model_copy(deep=True)


def _agent(*answers):
    agent = StoryAgent()
    agent.llm = FakeModel(*answers)
    return agent


def test_merging_keeps_every_line_and_the_length_and_renumbers():
    script = _script(5)
    lines = sum(len(s.dialogue) for s in script.scenes)
    total = sum(s.duration_ms for s in script.scenes)
    assert scene_gov.merge_to(script, 3) == 2
    assert [s.scene_id for s in script.scenes] == ["scene_1", "scene_2", "scene_3"]
    assert [s.index for s in script.scenes] == [0, 1, 2]
    assert sum(len(s.dialogue) for s in script.scenes) == lines
    assert sum(s.duration_ms for s in script.scenes) == total
    ids = [ln.line_id for s in script.scenes for ln in s.dialogue]
    assert len(set(ids)) == len(ids) and all(i.startswith("scene_") for i in ids)


def test_the_scene_count_asked_for_is_held(isolated_dirs):
    """The model wrote five scenes for a three-scene film: one repair, and if that is no better,
    the shortest neighbours are merged."""
    five, three = _script(5), _script(3)
    # repair works
    agent = _agent(three)
    script, repairs, merged = agent._fit_scenes(five, 3, 30)
    assert len(script.scenes) == 3 and repairs == 1 and merged == 0
    assert "exactly 3" in agent.llm.prompts[0]
    # repair answers wrongly: merged by the code
    agent = _agent(_script(4))
    script, repairs, merged = agent._fit_scenes(_script(5), 3, 30)
    assert len(script.scenes) == 3 and repairs == 1 and merged == 2
    # the provider fails: still three
    agent = _agent(RuntimeError("down"))
    script, repairs, merged = agent._fit_scenes(_script(5), 3, 30)
    assert len(script.scenes) == 3
    # already right: no call at all
    agent = _agent()
    script, repairs, merged = agent._fit_scenes(_script(3), 3, 30)
    assert (repairs, merged, len(script.scenes)) == (0, 0, 3) and not agent.llm.prompts


def test_too_few_scenes_are_reported_not_hidden(isolated_dirs):
    agent = _agent(_script(2))          # the repair cannot invent a story either
    script, repairs, merged = agent._fit_scenes(_script(2), 4, 30)
    check = scene_gov.scene_check(4, script, repairs, merged)
    assert check == {"asked": 4, "got": 2, "repairs": 1, "merged": 0, "within": False}


def test_the_story_records_the_creators_choices_and_the_checks(isolated_dirs):
    state = PipelineState(project_id="pid", user_prompt="A lighthouse keeper")
    agent = _agent(_script(4))                            # a model that wrote four for three
    agent.llm.provider = "fake"
    agent.run(state, target_duration_s=30, scene_count=3, format="9:16", style="anime")
    assert state.script.story.format == "9:16" and state.script.story.style == "anime"
    assert state.metadata["film_spec"] == {"format": "9:16", "style": "anime", "length_s": 30, "scenes": 3}
    assert state.metadata["scene_check"]["asked"] == 3
    assert len(state.script.scenes) == 3
    # and the model was told the frame and the style, never to name the medium
    prompt = agent.llm.prompts[0] if "9:16" in agent.llm.prompts[0] else ""
    # (the first prompt is the script prompt, built before the scene repair)


def test_the_script_prompt_tells_the_model_the_frame_and_to_leave_the_medium_alone(isolated_dirs):
    agent = _agent(_script(3))
    state = PipelineState(project_id="pid", user_prompt="A lighthouse keeper")
    agent.run(state, target_duration_s=30, scene_count=3, format="9:16", style="realistic")
    first = agent.llm.prompts[0]
    assert "tall 9:16" in first
    assert "palette, lighting and lens only" in first and "never name a medium" in first


# ---- the API --------------------------------------------------------------------------------

@pytest.fixture
def client(isolated_dirs):
    from backend import app as app_module
    return signed_in_client(app_module.app)


def test_the_api_validates_the_four_choices(client):
    ok = {"prompt": "A clockmaker in a flooded city", "target_duration_s": 30, "scene_count": 3}
    assert client.post("/api/pipeline/plan", json={**ok, "format": "4:3"}).status_code == 400
    assert client.post("/api/pipeline/plan", json={**ok, "style": "watercolour"}).status_code == 400
    assert client.post("/api/pipeline/plan", json={**ok, "target_duration_s": 5}).status_code == 422
    assert client.post("/api/pipeline/plan", json={**ok, "scene_count": 0}).status_code == 422
    assert client.post("/api/pipeline/run", json={**ok, "format": "huge"}).status_code == 400


def test_a_plan_is_queued_with_the_choices(client):
    import jobs
    run = client.post("/api/pipeline/plan", json={"prompt": "A clockmaker in a flooded city",
                                                  "target_duration_s": 30, "scene_count": 3,
                                                  "format": "9:16", "style": "anime"})
    assert run.status_code == 200
    payload = jobs.get(run.json()["job_id"]).payload
    assert payload["format"] == "9:16" and payload["style"] == "anime"
    # default: widescreen and realistic
    run = client.post("/api/pipeline/plan", json={"prompt": "A clockmaker in a flooded city"})
    payload = jobs.get(run.json()["job_id"]).payload
    assert payload["format"] == "16:9" and payload["style"] == "realistic"


@pytest.fixture
def storyboard(isolated_dirs, monkeypatch):
    """A planned (not rendered) 16:9 realistic film, in an account's reach."""
    from agents.orchestrator import PipelineOrchestrator
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from backend.services import pipeline_service

    orch = PipelineOrchestrator()
    state = orch.plan("A clockmaker in a flooded city", target_duration_s=24, scene_count=3,
                      project_id="t_spec", format="16:9", style="realistic")
    monkeypatch.setattr(pipeline_routes, "sm", orch.sm)
    monkeypatch.setattr(pipeline_service, "orchestrator", lambda: orch)
    return orch, state, signed_in_client(app_module.app)


def test_the_storyboard_says_its_format_and_style_and_the_scene_check(storyboard):
    _orch, _state, client = storyboard
    board = client.get("/api/pipeline/storyboard/t_spec").json()
    assert board["spec"]["format"] == "16:9" and board["spec"]["style"] == "realistic"
    assert board["spec"]["scene_check"]["asked"] == 3 and board["spec"]["scene_check"]["within"]


def test_changing_the_look_redraws_every_picture_in_the_new_shape_and_keeps_the_words(storyboard):
    from PIL import Image
    orch, state, _client = storyboard
    words = [ln.text for s in state.script.scenes for ln in s.dialogue]
    old_sig = state.storyboard.frames[0].preview_signature
    events = []
    out = orch.respec("t_spec", "9:16", "anime", on_event=events.append)
    assert out.script.story.format == "9:16" and out.script.story.style == "anime"
    assert [ln.text for s in out.script.scenes for ln in s.dialogue] == words        # words kept
    for frame in out.storyboard.frames:
        assert Image.open(frame.preview_path).size == (720, 1280)                    # redrawn, tall
    assert out.storyboard.frames[0].preview_signature != old_sig                     # the render will not reuse the old look
    assert [e.status for e in events if e.phase == "storyboard"][-1] == "complete"
    assert {"frame", "portrait"} <= {e.status for e in events}


def test_a_picture_reused_by_the_render_is_only_ever_in_the_look_that_was_chosen(storyboard):
    from agents.video_agent import VideoAgent
    orch, state, _client = storyboard
    agent = VideoAgent()
    scene = state.script.scenes[0]
    w, h = video_quality.image_size(1920, 1080)
    assert agent.reusable_preview(state, scene, w, h)                    # same spec: reusable
    state.script.story.style = "anime"
    assert agent.reusable_preview(state, scene, w, h) is None            # another style: drawn fresh
    state.script.story.style = "realistic"
    state.script.story.format = "9:16"
    assert agent.reusable_preview(state, scene, w, h) is None            # another shape too


def test_the_api_changes_the_spec_only_before_the_render(storyboard, small_project):
    _orch, _state, client = storyboard
    assert client.patch("/api/pipeline/spec/t_spec", json={}).status_code == 400
    assert client.patch("/api/pipeline/spec/t_spec", json={"format": "4:3"}).status_code == 400
    queued = client.patch("/api/pipeline/spec/t_spec", json={"style": "anime"})
    assert queued.status_code == 200 and queued.json()["job_id"]
    # one change at a time
    assert client.patch("/api/pipeline/spec/t_spec", json={"format": "9:16"}).status_code == 409


def test_a_rendered_film_cannot_be_respecced(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from agents.orchestrator import PipelineOrchestrator
    state, sm = small_project(project_id="t_done")
    monkeypatch.setattr(pipeline_routes, "sm", sm)
    client = signed_in_client(app_module.app)
    assert client.patch("/api/pipeline/spec/t_done", json={"style": "anime"}).status_code == 409
    orch = PipelineOrchestrator()
    orch.sm = sm
    with pytest.raises(ValueError, match="already rendered"):
        orch.respec("t_done", "16:9", "anime")


def test_the_library_and_the_film_name_their_format(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from backend.routes import projects as project_routes
    state, sm = small_project(project_id="t_tall", format="9:16", style="anime")
    monkeypatch.setattr(pipeline_routes, "sm", sm)
    monkeypatch.setattr(project_routes, "sm", sm)
    client = signed_in_client(app_module.app)
    film = client.get("/api/pipeline/film/t_tall").json()
    assert film["format"] == "9:16" and film["style"] == "anime"
    assert (film["width"], film["height"]) == SMALL["9:16"]
    row = next(r for r in client.get("/api/projects/").json() if r["project_id"] == "t_tall")
    assert row["format"] == "9:16"


def test_a_story_written_again_keeps_the_choices(small_project):
    from agents.edit_agent import EditAgent
    from shared.schemas.edit import EditCommand
    from tests.conftest import silence_tts
    state, sm = small_project(project_id="t_again", format="9:16", style="anime")
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    agent.edit(EditCommand(project_id="t_again", query="rewrite the script"))
    latest = sm.latest("t_again")
    assert latest.script.story.format == "9:16" and latest.script.story.style == "anime"


# ---- recorded with the job's result ------------------------------------------------------------

def test_the_plan_result_records_format_style_and_a_scene_count_that_was_not_met(isolated_dirs):
    from agents.orchestrator import PipelineOrchestrator

    orch = PipelineOrchestrator()
    events = []
    state = orch.plan("A clockmaker in a flooded city", target_duration_s=24, scene_count=3,
                      format="1:1", style="anime", on_event=events.append)
    done = events[-1]
    assert done.status == "complete" and done.payload["format"] == "1:1" and done.payload["style"] == "anime"
    assert "scene_count_warning" not in done.payload
    # A script that could not be brought to the count says so, in the message and the payload.
    state.metadata["scene_check"] = {"asked": 4, "got": 3, "repairs": 1, "merged": 0, "within": False}
    note, payload = orch._completion_notes(PipelineState(project_id="x", user_prompt="p",
                                                         script=state.script, metadata=state.metadata))
    assert "3 scenes for the 4 asked" in note
    assert payload["scene_count_warning"]["got"] == 3
