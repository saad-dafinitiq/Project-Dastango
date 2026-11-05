"""DST-36 — a scene is shot like a scene: several shots, cut on the dialogue, each with a
camera move that matches what it is doing, and the storyboard shows them.

Pure planning is tested directly (it is fast and exact); the rest goes through a real
render, because a plan that never reaches the picture proves nothing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml

from agents.video_agent import camera, coverage
from agents.video_agent.coverage import CoverLine, plan_coverage, shots_for
from shared import video_quality


def L(who, start, end, narrator=False, text="Hello there."):
    return CoverLine(who, narrator, start, end, text)


# ---- how many shots ---------------------------------------------------------------

@pytest.mark.parametrize("nominal_ms,speakers,lines,expected", [
    (6_000, 0, 0, 2),        # short scene, nobody talking: the minimum
    (10_000, 1, 1, 2),       # one line: the wide and that line
    (12_000, 1, 0, 3),       # length alone
    (20_000, 0, 0, 4),       # long: the maximum
    (90_000, 0, 0, 4),       # however long, never more than 4
    (10_000, 2, 0, 3),       # two people talking: the wide and a shot each
    (10_000, 2, 2, 3),
    (12_000, 2, 3, 4),       # a shot per line, up to the cap
    (12_000, 2, 9, 4),
    (4_000, 2, 3, 2),        # but never shorter than the shortest shot allows
])
def test_the_number_of_shots_comes_from_length_speakers_and_lines(nominal_ms, speakers, lines, expected):
    assert shots_for(nominal_ms, speakers, lines) == expected


def test_a_scene_is_always_two_to_four_shots_whatever_it_holds():
    q = video_quality.get()
    for total in (3_000, 5_000, 8_000, 12_000, 20_000, 40_000):
        for lines in range(0, 7):
            cast = ["A", "B", "C"]
            ls, t = [], 1500
            for i in range(lines):
                ls.append(L(cast[i % 3], t, t + 1800))
                t += 2050
            covers = plan_coverage(0, max(total, t + 600), ls, "calm", total, 0)
            assert q.min_shots <= len(covers) <= q.max_shots, (total, lines, len(covers))


def test_a_long_scene_gets_a_cutaway_picture_and_a_short_one_does_not():
    assert coverage.needs_cutaway(14_000) and not coverage.needs_cutaway(13_900)


# ---- where the cuts fall ----------------------------------------------------------------

def _scene():
    """Pre-roll, a narrator, then A, B, A: the shape of a real scene."""
    return [L("n", 1500, 4000, narrator=True), L("A", 4250, 6500),
            L("B", 6750, 9000), L("A", 9250, 11000)]


def test_the_shots_tile_the_scene_with_no_gap_or_overlap():
    covers = plan_coverage(10_000, 21_600, [L("n", 11_500, 14_000, True), L("A", 14_250, 16_500),
                                            L("B", 16_750, 21_000)], "calm", 12_000, 0)
    assert covers[0].start_ms == 10_000 and covers[-1].end_ms == 21_600
    assert all(a.end_ms == b.start_ms for a, b in zip(covers, covers[1:]))
    assert all(c.duration_ms > 0 for c in covers)


def test_the_scene_opens_on_a_wide_shot_that_pans_and_closes_on_a_pull_out():
    for index in (0, 1, 2, 3):
        covers = plan_coverage(0, 11_600, _scene(), "calm", 12_000, index)
        assert covers[0].framing == "wide" and covers[0].role == "establishing"
        assert covers[0].move == ("pan_right" if index % 2 == 0 else "pan_left")
        assert covers[-1].move == "pull_out"


def test_dialogue_cuts_to_the_speaker_and_back():
    """Shot and reverse shot: the close-up of whoever speaks, on the moment they start."""
    lines = [L("A", 1500, 4000), L("B", 4250, 6500), L("A", 6750, 9000)]
    covers = plan_coverage(0, 9600, lines, "calm", 10_000, 0)
    closes = [c for c in covers if c.framing == "close"]
    assert [c.character_id for c in closes] == ["A", "B", "A"][:len(closes)]
    for c in closes:
        speaking = [ln for ln in lines if ln.start_ms <= c.start_ms < ln.end_ms + 250]
        assert speaking and speaking[0].character_id == c.character_id, c


def test_a_close_up_is_never_of_someone_who_is_not_speaking_when_it_starts():
    for tone in ("calm", "tense", "joyful"):
        covers = plan_coverage(0, 11_600, _scene(), tone, 12_000, 1)
        for c in (c for c in covers if c.framing == "close"):
            speaker = next(ln for ln in _scene() if ln.start_ms <= c.start_ms < ln.end_ms + 250)
            assert speaker.character_id == c.character_id


def test_every_line_starts_on_a_cut_while_the_cap_allows_it():
    """A scene of up to three lines has a shot for the wide and each of them."""
    lines = [L("A", 1500, 3500), L("B", 3750, 5750), L("A", 6000, 8000)]
    cuts = {c.start_ms for c in plan_coverage(0, 8600, lines, "calm", 9_000, 0)}
    assert all(ln.start_ms in cuts for ln in lines)


def test_two_cuts_in_a_row_never_land_on_the_same_picture_and_framing():
    shapes = [
        _scene(),
        [L("A", 1500, 4000), L("A", 4250, 6500), L("A", 6750, 9000)],      # one speaker, three lines
        [L("n", 1500, 3500, True), L("n", 3750, 5750, True), L("n", 6000, 8000, True)],
    ]
    for lines in shapes:
        covers = plan_coverage(0, lines[-1].end_ms + 600, lines, "calm", 14_000, 0, has_cutaway=True)
        for a, b in zip(covers, covers[1:]):
            assert (a.source, a.crop, a.character_id) != (b.source, b.crop, b.character_id), (a, b)


def test_a_speaker_with_no_portrait_is_the_world_not_a_blank_face():
    covers = plan_coverage(0, 8000, [L("ghost", 1500, 4000), L("A", 4250, 7400)], "calm", 9000, 0,
                           has_portrait=lambda cid: cid == "A")
    assert all(c.character_id != "ghost" for c in covers)


def test_a_long_scene_cuts_away_to_its_detail_picture_when_it_has_one():
    covers = plan_coverage(0, 20_000, [], "calm", 20_000, 0, has_cutaway=True)
    assert [c.framing for c in covers].count("cutaway") == 1
    assert plan_coverage(0, 20_000, [], "calm", 20_000, 0, has_cutaway=False)[0].framing == "wide"
    assert all(c.framing != "cutaway"
               for c in plan_coverage(0, 20_000, [], "calm", 20_000, 0, has_cutaway=False))


def test_a_medium_shot_is_a_crop_of_the_wide_picture_not_another_image():
    covers = plan_coverage(0, 11_600, _scene(), "calm", 12_000, 0)
    mediums = [c for c in covers if c.framing == "medium"]
    assert mediums and all(c.source == "wide" and c.crop for c in mediums)
    width = video_quality.get().medium_crop
    assert all(c.crop[0] == width and 0 <= c.crop[1] <= 1 and 0 <= c.crop[2] <= 1 for c in mediums)
    assert all(c.crop is None for c in covers if c.framing != "medium")


# ---- the move chosen for each kind of shot -----------------------------------------------

def test_each_kind_of_shot_gets_the_move_for_its_job():
    assert camera.move_for_role("establishing", scene_index=0) == "pan_right"
    assert camera.move_for_role("establishing", scene_index=1) == "pan_left"
    assert camera.move_for_role("dialogue", "calm", emotional=False) == "handheld"
    assert camera.move_for_role("dialogue", "calm", emotional=True) == "push_in"
    assert camera.move_for_role("cutaway") == "drift"
    assert camera.move_for_role("ending", "tense", emotional=True) == "pull_out"
    # Narration pans the other way from the pan before it.
    assert (camera.move_for_role("narration", scene_index=0, shot_index=1)
            != camera.move_for_role("establishing", scene_index=0))


@pytest.mark.parametrize("tone,text,expected", [
    ("calm", "I think we should go.", False),
    ("calm", "Get out!", True),
    ("calm", "I just... I don't know.", True),
    ("calm", "Do NOT go.", True),
    ("calm", "Wait for ME.", False),           # a two-letter word is not shouting
    ("tense", "We go at dawn.", True),
    ("melancholic", "It is quiet now.", True),
    ("joyful", "What a lovely day.", False),
    ("", "", False),
])
def test_an_emotional_line_is_read_from_the_tone_and_the_punctuation(tone, text, expected):
    assert camera.is_emotional(tone, text) is expected


def test_a_dialogue_shot_in_a_calm_scene_breathes_and_in_a_heavy_one_pushes_in():
    calm = plan_coverage(0, 8000, [L("A", 1500, 4000), L("B", 4250, 7400)], "joyful", 9000, 0)
    heavy = plan_coverage(0, 8000, [L("A", 1500, 4000), L("B", 4250, 7400)], "tense", 9000, 0)
    assert [c.move for c in calm if c.framing == "close"][0] == "handheld"
    assert [c.move for c in heavy if c.framing == "close"][0] == "push_in"


# ---- the camera itself ----------------------------------------------------------------------

def _picture(path, w=320, h=180, border=None):
    from PIL import Image
    rng = np.random.default_rng(3)
    base = rng.integers(60, 190, size=(h // 6, w // 6, 3), dtype=np.uint8)
    img = Image.fromarray(base).resize((w, h), Image.BICUBIC)
    if border:
        arr = np.array(img)
        arr[:3, :], arr[-3:, :], arr[:, :3], arr[:, -3:] = border, border, border, border
        img = Image.fromarray(arr)
    img.save(path)
    return path


def _frames(path, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h},format=rgb24",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)


def _render(image, chain, frames, out, fps=24):
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(image), "-vf", chain + ",format=yuv420p",
                    "-frames:v", str(frames), "-r", str(fps), "-c:v", "libx264", "-crf", "8",
                    "-preset", "veryfast", "-an", str(out)], check=True, capture_output=True)
    return out


def _shift(a, b):
    """The (dx, dy) that moves grey frame `a` onto `b`, to a fraction of a pixel (FFT phase correlation)."""
    fa, fb = np.fft.fft2(a), np.fft.fft2(b)
    cross = fa * np.conj(fb)
    corr = np.fft.ifft2(cross / (np.abs(cross) + 1e-9)).real
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)

    def refine(values, i):
        n = len(values)
        l, c, r = values[(i - 1) % n], values[i], values[(i + 1) % n]
        denom = l - 2 * c + r
        return (0.5 * (l - r) / denom) if denom else 0.0
    dy = iy + refine(corr[:, ix], iy)
    dx = ix + refine(corr[iy, :], ix)
    h, w = a.shape
    return (dx - w if dx > w / 2 else dx), (dy - h if dy > h / 2 else dy)


def test_a_handheld_shot_sways_smoothly_and_never_shows_the_pictures_edge(tmp_path):
    image = _picture(tmp_path / "p.png", border=(255, 0, 0))
    out = _render(image, camera.motion_filter("handheld", 48, 320, 180, 24, seed=4242), 48,
                  tmp_path / "h.mp4")
    frames = _frames(out, 320, 180)
    assert len(frames) == 48
    # The picture's own colours stay between 60 and 190, so only its border is this red.
    red = (frames[..., 0] > 235) & (frames[..., 1] < 20) & (frames[..., 2] < 20)
    assert not red.any(), "the sway reached the edge of the picture"

    def motion(chain, name):
        clip = _frames(_render(image, chain, 48, tmp_path / f"{name}.mp4"), 320, 180)
        grey = clip.mean(axis=3).astype(np.float32)
        return np.array([_shift(grey[i], grey[i + 1]) for i in range(len(grey) - 1)])

    shifts = motion(camera.motion_filter("handheld", 48, 320, 180, 24, seed=4242), "h2")
    travel = np.abs(shifts).max()
    assert 0.02 < travel < 2.0, f"a sway of {travel:.2f} px a frame is not a gentle one"
    # Smooth: no jerkier than an eased pan of the same picture, measured the same way
    # (the measure itself reads about 0.7 px of noise on a 320 px frame, so the figure
    # that means something is the comparison, not the number).
    pan = motion(camera.motion_filter("pan_right", 48, 320, 180, 24), "pan")
    jerk = np.abs(np.diff(shifts, axis=0)).max()
    assert jerk <= 1.3 * np.abs(np.diff(pan, axis=0)).max(), f"jerky: {jerk:.2f} px between steps"


def test_two_handheld_shots_do_not_sway_in_step():
    a = camera.motion_filter("handheld", 48, 320, 180, 24, seed=11)
    b = camera.motion_filter("handheld", 48, 320, 180, 24, seed=99999)
    assert a != b
    assert camera.motion_filter("handheld", 48, 320, 180, 24, seed=11) == a     # but repeatable


def test_a_crop_frames_part_of_the_picture_before_the_one_enlargement():
    chain = camera.motion_filter("static_hold", 12, 1920, 1080, 24, crop=(0.72, 0.28, 0.42))
    assert chain.startswith("crop=")
    assert chain.index("crop=w=trunc(iw*0.7200") < chain.index("scale=w=")      # crop, then enlarge
    assert chain.count("force_original_aspect_ratio=increase") == 1
    assert not camera.motion_filter("static_hold", 12, 1920, 1080, 24).startswith("crop=")
    assert not camera.motion_filter("static_hold", 12, 1920, 1080, 24, crop=(1.0, 0, 0)).startswith("crop=")


def test_a_crop_shows_only_the_part_it_asked_for(tmp_path):
    """Four colour quadrants: a half-width crop at the top left shows only the top-left colour."""
    from PIL import Image
    arr = np.zeros((180, 320, 3), dtype=np.uint8)
    arr[:90, :160], arr[:90, 160:] = (230, 30, 30), (30, 230, 30)
    arr[90:, :160], arr[90:, 160:] = (30, 30, 230), (230, 230, 30)
    Image.fromarray(arr).save(tmp_path / "q.png")
    chain = camera.motion_filter("static_hold", 6, 320, 180, 24, crop=(0.5, 0.0, 0.0))
    frames = _frames(_render(tmp_path / "q.png", chain, 6, tmp_path / "q.mp4"), 320, 180)
    middle = frames[3, 60:120, 100:220].reshape(-1, 3).mean(axis=0)
    assert middle[0] > 170 and middle[1] < 90 and middle[2] < 90, middle     # red: the top-left quadrant


# ---- config ---------------------------------------------------------------------------------------

def _config(tmp_path, monkeypatch, **overrides):
    data = yaml.safe_load((video_quality.DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
    for key, value in overrides.items():
        section, name = key.split("__")
        data[section][name] = value
    path = tmp_path / "video.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_shot_settings_are_read_from_the_config(tmp_path, monkeypatch):
    path = _config(tmp_path, monkeypatch, coverage__max_shots=3, coverage__medium_crop=0.8,
                   handheld__sway=0.2)
    q = video_quality.load(path)
    assert (q.max_shots, q.medium_crop, q.handheld_sway) == (3, 0.8, 0.2)
    monkeypatch.setenv("VIDEO_CONFIG_FILE", str(path))
    video_quality._cache.clear()
    assert shots_for(60_000, 0, 0) == 3                      # the cap moved with the file
    video_quality._cache.clear()


@pytest.mark.parametrize("override,named", [
    ({"coverage__max_shots": 1, "coverage__min_shots": 2}, "max_shots"),
    ({"coverage__medium_crop": 0.1}, "medium_crop"),
    ({"coverage__typical_shot_s": "four"}, "typical_shot_s"),
    ({"handheld__zoom": 2.0}, "handheld.zoom"),
])
def test_a_wrong_shot_setting_fails_naming_itself(tmp_path, monkeypatch, override, named):
    with pytest.raises(ValueError, match=named):
        video_quality.load(_config(tmp_path, monkeypatch, **override))


# ---- through a real render --------------------------------------------------------------------------

def test_a_rendered_film_is_covered_and_every_line_is_on_a_cut(small_project):
    state, _ = small_project(duration_s=24, scenes=3)
    q = video_quality.get()
    people = {c.id: c for c in state.script.characters.characters}
    cuts = {s.start_ms for f in state.video.frames for s in f.shots}
    for frame in state.video.frames:
        assert q.min_shots <= len(frame.shots) <= q.max_shots, frame.scene_id
        assert frame.shots[0].framing == "wide" and frame.shots[0].kind == "establishing"
        assert frame.shots[-1].motion == "pull_out"
        for shot in frame.shots:
            assert shot.framing in ("wide", "medium", "close", "cutaway")
            assert (shot.crop is not None) == (shot.framing == "medium")
            if shot.framing == "close":
                assert people[shot.character_id].role != "narrator"
    assert all(seg.start_ms in cuts for seg in state.audio.manifest.segments)


def test_the_saved_shots_say_how_they_are_framed(small_project):
    state, sm = small_project(duration_s=24, scenes=3)
    saved = sm.latest(state.project_id)
    shots = [s for f in saved.video.frames for s in f.shots]
    assert all(s.framing for s in shots)
    assert any(s.crop for s in shots) or all(s.framing != "medium" for s in shots)


def test_a_film_saved_before_shots_had_framing_still_loads():
    from shared.schemas.video import Shot
    old = Shot(shot_id="s", scene_id="x", image_path="p.png", duration_ms=1000)
    assert old.framing == "" and old.crop is None


def test_a_scene_with_a_cutaway_picture_cuts_to_it_in_the_render(small_project):
    """A 20 s scene is long enough for a second, detail picture, and a long hold is cut to it."""
    state, _ = small_project(duration_s=40, scenes=2)
    assert all(len(f.shot_bank) == 2 for f in state.video.frames)
    cutaways = [s for f in state.video.frames for s in f.shots if s.framing == "cutaway"]
    for shot in cutaways:
        frame = next(f for f in state.video.frames if f.scene_id == shot.scene_id)
        assert shot.image_path == frame.shot_bank[1] and shot.kind == "broll"


# ---- the storyboard shows the shots ---------------------------------------------------------------------

def _cards(script):
    from agents.orchestrator.storyboard_view import scene_cards
    return scene_cards(script, engine="edge")


def test_every_storyboard_card_carries_the_scenes_shots():
    from agents.story_agent.planner import template_script
    script = template_script("p", "A robot learns to paint", target_duration_s=30, scene_count=3)
    q = video_quality.get()
    for card in _cards(script):
        shots = card["shots"]
        assert q.min_shots <= len(shots) <= q.max_shots
        assert card["move"] == shots[0]["move"]                       # the card names the opening move
        assert shots[0]["framing"] == "wide"
        assert [s["start_ms"] for s in shots][0] == 0
        assert all(a["end_ms"] == b["start_ms"] for a, b in zip(shots, shots[1:]))
        for s in shots:
            assert set(s) >= {"shot_id", "framing", "source", "move", "character_id", "character",
                              "start_ms", "end_ms", "crop"}
            assert (s["crop"] is not None) == (s["framing"] == "medium")
            if s["framing"] == "close":
                assert s["character"]


def test_the_storyboard_plan_is_the_one_the_render_cuts(small_project):
    """Same planner, so the storyboard's framings and moves are the render's, scene by scene."""
    state, _ = small_project(duration_s=24, scenes=3)
    cards = {c["scene_id"]: c for c in _cards(state.script)}
    for frame in state.video.frames:
        planned = cards[frame.scene_id]["shots"]
        rendered = frame.shots
        assert [s["framing"] for s in planned] == [s.framing for s in rendered], frame.scene_id
        assert [s["move"].replace(" ", "_") for s in planned] == [s.motion for s in rendered]


def test_the_streamed_cards_and_the_reloaded_storyboard_agree_on_the_shots(isolated_dirs, monkeypatch):
    from agents.orchestrator import PipelineOrchestrator
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from state_manager.state_manager import StateManager
    from state_manager.storage import VersionStore
    from tests.conftest import signed_in_client

    orch = PipelineOrchestrator(state_manager=StateManager(VersionStore(isolated_dirs / "state.db")))
    events = []
    state = orch.plan("A clockmaker in a flooded city repairs the hours people lose",
                      on_event=events.append, target_duration_s=20, scene_count=2)
    streamed = next(e for e in events if (e.phase, e.status) == ("storyboard", "script")).payload["scenes"]
    assert all(card["shots"] for card in streamed)

    monkeypatch.setattr(pipeline_routes, "sm", orch.sm)
    board = signed_in_client(app_module.app).get(f"/api/pipeline/storyboard/{state.project_id}").json()
    for card, frame in zip(streamed, board["frames"]):
        assert frame["shots"] == card["shots"]


def test_more_shots_do_not_cost_more_images():
    from agents.orchestrator.storyboard_view import image_budget
    from agents.story_agent.planner import template_script
    short = template_script("p", "A robot learns to paint", target_duration_s=30, scene_count=3)
    long = template_script("p", "A robot learns to paint", target_duration_s=60, scene_count=3)
    people = len(short.characters.characters)
    assert image_budget(short) == {"plan": 3 + people, "render": 0}       # 10 s scenes: wide and crops only
    assert image_budget(long) == {"plan": 3 + people, "render": 3}        # 20 s scenes: one cutaway each
