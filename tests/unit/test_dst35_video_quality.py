"""DST-35: finished films are sharp.

A film used to be 720p, enlarged by ffmpeg's default filter inside the camera,
moved at a constant speed and encoded up to four times at CRF 22 with the veryfast
preset (782 kb/s for a 720p film). Now the output is 1080p, every enlargement is a
Lanczos one made once before the camera moves, moves ease in and out, a light
sharpen restores the acuity enlarging softens, and only the last encode is the
delivered quality. Every one of those numbers is in config/video.yaml.

Real ffmpeg runs where the claim is about pixels; spied commands where it is about
which setting reached which stage.
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from agents.video_agent import animator, camera
from agents.video_agent.agent import VideoAgent
from mcp.tools.video_tools import compositor_tool, subtitle_tool
from shared import constants, video_quality

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "config" / "video.yaml"


def _settings(**changes) -> dict:
    cfg = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    for key, value in changes.items():
        section, name = key.split("__")
        cfg[section][name] = value
    return cfg


@pytest.fixture
def use_config(tmp_path, monkeypatch):
    """Point the settings at a temporary file with the given changes."""
    def apply(**changes):
        path = tmp_path / "video.yaml"
        path.write_text(yaml.safe_dump(_settings(**changes)), encoding="utf-8")
        monkeypatch.setenv("VIDEO_CONFIG_FILE", str(path))
        # Two rewrites inside one test can share a modification time (Windows keeps
        # about 15 ms), so the cache is cleared rather than trusted to notice.
        video_quality._cache.clear()
        return video_quality.get()
    return apply


def _ffprobe(path: Path, entries: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", f"stream={entries}", "-of", "default=nw=1", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _gray_frames(path: Path, width: int, height: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={width}:{height},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width).astype(np.float32)


def _picture(path: Path, width: int, height: int, kind: str = "edges") -> Path:
    from PIL import Image
    if kind == "line":                     # a thin white line on black: its position is the pan
        data = np.zeros((height, width, 3), dtype=np.uint8)
        data[:, width // 2 - 2: width // 2 + 2] = 255
    else:                                  # hard edges: a checkerboard
        cells = (np.indices((height // 6 + 1, width // 6 + 1)).sum(axis=0) % 2) * 255
        data = np.kron(cells, np.ones((6, 6)))[:height, :width].astype(np.uint8)
        data = np.stack([data] * 3, axis=-1)
    Image.fromarray(data).save(path)
    return path


# ---- the settings file ---------------------------------------------------------------

def test_the_shipped_settings_load_with_the_values_the_measurements_chose():
    q = video_quality.load(SHIPPED)
    assert q.upscale_filter == "lanczos" and q.easing == "smoothstep"
    assert q.pix_fmt == "yuv420p" and q.final_profile == "high"
    assert q.image_long_side == 1280 and q.upscale_max_pixels == 8_500_000


def test_every_setting_has_a_reason_written_next_to_it():
    lines = SHIPPED.read_text(encoding="utf-8").splitlines()

    def is_value(line):
        return (not line.lstrip().startswith("#") and ":" in line
                and line.split(":", 1)[1].strip() != "")

    for number, line in enumerate(lines):
        if not is_value(line) or line.startswith("version"):
            continue
        above = number - 1
        while above >= 0 and is_value(lines[above]):
            above -= 1
        assert lines[above].lstrip().startswith("#"), f"no reason above {line.strip()!r}"


@pytest.mark.parametrize("change, message", [
    ({"upscale__filter": "nearest"}, "upscale.filter"),
    ({"upscale__factor": 9}, "upscale.factor"),
    ({"upscale__factor": 0.5}, "upscale.factor"),
    ({"upscale__max_pixels": 10}, "upscale.max_pixels"),
    ({"motion__easing": "bounce"}, "motion.easing"),
    ({"sharpen__luma_amount": 5}, "sharpen.luma_amount"),
    ({"sharpen__luma_amount": -1}, "sharpen.luma_amount"),
    ({"encode__intermediate_crf": 40}, "encode.intermediate_crf"),
    ({"encode__final_crf": "good"}, "encode.final_crf"),
    ({"encode__final_crf": True}, "encode.final_crf"),
    ({"encode__final_preset": "ludicrous"}, "encode.final_preset"),
    ({"encode__final_profile": "baseline"}, "encode.final_profile"),
    ({"encode__pix_fmt": "yuv444p"}, "encode.pix_fmt"),
    ({"images__long_side": 100}, "images.long_side"),
])
def test_a_wrong_setting_fails_loudly_naming_itself(tmp_path, change, message):
    path = tmp_path / "video.yaml"
    path.write_text(yaml.safe_dump(_settings(**change)), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        video_quality.load(path)


def test_a_missing_section_is_named(tmp_path):
    cfg = _settings()
    del cfg["encode"]
    path = tmp_path / "video.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match="section 'encode'"):
        video_quality.load(path)


def test_the_settings_are_read_again_when_the_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "video.yaml"
    monkeypatch.setenv("VIDEO_CONFIG_FILE", str(path))
    path.write_text(yaml.safe_dump(_settings(encode__final_crf=17)), encoding="utf-8")
    assert video_quality.get().final_crf == 17
    path.write_text(yaml.safe_dump(_settings(encode__final_crf=23)), encoding="utf-8")
    import os
    os.utime(path, ns=(os.stat(path).st_atime_ns, os.stat(path).st_mtime_ns + 5_000_000))
    assert video_quality.get().final_crf == 23                     # no restart needed


# ---- encode settings come from config, at every stage -----------------------------------

def test_the_encoder_arguments_are_the_configured_ones(use_config):
    use_config(encode__intermediate_crf=11, encode__intermediate_preset="faster",
               encode__final_crf=16, encode__final_preset="slow", encode__final_profile="main")
    assert video_quality.x264_args("intermediate") == [
        "-c:v", "libx264", "-preset", "faster", "-crf", "11", "-pix_fmt", "yuv420p"]
    assert video_quality.x264_args("final") == [
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-profile:v", "main",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    with pytest.raises(ValueError, match="unknown encode stage"):
        video_quality.x264_args("best")


def test_no_command_in_the_pipeline_carries_a_crf_of_its_own():
    offenders = []
    for folder in ("agents", "mcp", "shared", "backend", "jobs"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.name == "video_quality.py":
                continue
            if re.search(r"""["']-crf["']""", path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"hard-coded encode settings in: {offenders}"


def _record(monkeypatch, module):
    seen = []

    def run(cmd, *a, **kw):
        seen.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(module.subprocess, "run", run)
    return seen


def _flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def test_each_stage_encodes_with_the_setting_for_its_place(tmp_path, monkeypatch, use_config):
    use_config(encode__intermediate_crf=9, encode__intermediate_preset="faster",
               encode__final_crf=19, encode__final_preset="fast")

    # A shot: intermediate.
    shot_cmds = []
    monkeypatch.setattr(animator, "_run_shot",
                        lambda cmd, check: shot_cmds.append([str(c) for c in cmd])
                        or subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b""))
    image = _picture(tmp_path / "p.png", 160, 90)
    animator.render_shot(animator.Shot(str(image), 1000, "drift", frames=24), tmp_path / "s.mp4",
                         320, 180, 24)
    assert (_flag(shot_cmds[0], "-crf"), _flag(shot_cmds[0], "-preset")) == ("9", "faster")

    # A scene join: intermediate.
    seen = _record(monkeypatch, animator)
    animator.assemble_scene([tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "scene.mp4",
                            durations_s=[1.0, 1.0])
    assert (_flag(seen[0], "-crf"), _flag(seen[0], "-preset")) == ("9", "faster")

    # The compose pass: the setting it is asked for.
    seen = _record(monkeypatch, compositor_tool)
    for quality, expected in (("intermediate", ("9", "faster")), ("final", ("19", "fast"))):
        compositor_tool.CompositorTool()._xfade_chain(
            ["a.mp4", "b.mp4"], tmp_path / "c.mp4", None, 0.4, durations_s=[1.0, 1.0],
            quality=quality)
        assert (_flag(seen[-1], "-crf"), _flag(seen[-1], "-preset")) == expected

    # Burned subtitles: always the delivered quality.
    seen = _record(monkeypatch, subtitle_tool)
    subtitle_tool.SubtitleTool().run(str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"),
                                     [{"start_ms": 0, "end_ms": 500, "text": "hello"}])
    assert (_flag(seen[0], "-crf"), _flag(seen[0], "-preset")) == ("19", "fast")
    assert "+faststart" in seen[0] and _flag(seen[0], "-profile:v") == "high"


def test_a_speed_change_encodes_at_the_quality_it_is_given(tmp_path, monkeypatch, use_config):
    from agents.video_agent import agent as video_agent
    use_config(encode__intermediate_crf=9, encode__final_crf=19)
    seen = _record(monkeypatch, video_agent)
    VideoAgent._apply_speed(tmp_path / "a.mp4", tmp_path / "b.mp4", 1.5, has_audio=False,
                            quality="intermediate")
    VideoAgent._apply_speed(tmp_path / "a.mp4", tmp_path / "b.mp4", 1.5, has_audio=False)
    assert _flag(seen[0], "-crf") == "9" and _flag(seen[1], "-crf") == "19"


# ---- only the last pass is the delivered quality ----------------------------------------

def _state(speed=1.0, subtitles=True, burn=True, lines=True):
    segments = [SimpleNamespace(kind="dialogue", text="hello" if lines else "")]
    return SimpleNamespace(
        project_id="p35",
        video=SimpleNamespace(fps=24, speed_factor=speed, has_subtitles=subtitles,
                              burn_subtitles=burn, subtitle_languages=[], subtitles_skipped=[],
                              frames=[SimpleNamespace(clip_path="a.mp4",
                                                      shots=[SimpleNamespace(render_frames=48)])]),
        audio=SimpleNamespace(master_track=None, manifest=SimpleNamespace(segments=segments)))


@pytest.mark.parametrize("kwargs, burn", [
    ({}, True),
    ({"subtitles": False}, False),
    ({"burn": False}, False),
    ({"lines": False}, False),          # nothing to burn: no later pass
])
def test_the_agent_knows_whether_a_subtitle_pass_will_follow(kwargs, burn):
    assert VideoAgent._will_burn_subtitles(_state(**kwargs)) is burn


def test_a_film_with_no_audio_burns_nothing():
    state = _state()
    state.audio = None
    assert VideoAgent._will_burn_subtitles(state) is False


@pytest.mark.parametrize("kwargs, compose, speed", [
    ({}, "intermediate", None),                          # burned subtitles follow
    ({"subtitles": False}, "final", None),               # compose is the last pass
    ({"speed": 1.5}, "intermediate", "intermediate"),    # speed, then burned subtitles
    ({"speed": 1.5, "subtitles": False}, "intermediate", "final"),
])
def test_only_the_last_pass_is_encoded_at_the_delivered_quality(isolated_dirs, monkeypatch,
                                                                kwargs, compose, speed):
    agent = VideoAgent()
    calls = []

    class Tools:
        def execute(self, name, **kw):
            calls.append((name, kw))
            return SimpleNamespace(success=True, metadata={}, data=None)
    agent.tools = Tools()
    speeds = []
    monkeypatch.setattr(VideoAgent, "_apply_speed",
                        staticmethod(lambda src, dst, factor, has_audio, quality="final":
                                     speeds.append(quality) or dst))
    monkeypatch.setattr(agent, "_embed_subtitles", lambda state, path: None)
    agent._compose_final(_state(**kwargs))
    assert calls[0][1]["quality"] == compose
    assert (speeds[0] if speeds else None) == speed


# ---- the output size ----------------------------------------------------------------------

def test_a_new_film_is_1080p():
    from agents.orchestrator.state import RunContext
    from shared.schemas.video import VideoOutput
    assert (constants.DEFAULT_WIDTH, constants.DEFAULT_HEIGHT) == (1920, 1080)
    ctx = RunContext(state=SimpleNamespace())
    assert (ctx.width, ctx.height) == (1920, 1080)
    film = VideoOutput(project_id="p", frames=[], final_video_path="")
    assert (film.width, film.height) == (1920, 1080)


def test_a_film_saved_at_720p_keeps_its_size():
    from shared.schemas.video import VideoOutput
    old = VideoOutput.model_validate({"project_id": "p", "frames": [], "final_video_path": "",
                                      "width": 1280, "height": 720})
    assert (old.width, old.height) == (1280, 720)


def test_the_pictures_stay_at_the_picture_size_while_the_film_goes_to_1080p():
    """Storing 1920x1080 pictures cost 1.85x the disk (32.8 MB to 60.8 MB for one film's
    28) and holds no detail the camera does not add: so only the output grows."""
    assert video_quality.image_size(1920, 1080) == (1280, 720)
    assert video_quality.image_size(1080, 1920) == (720, 1280)        # portrait keeps its shape
    assert video_quality.image_size(1280, 720) == (1280, 720)
    assert video_quality.image_size(320, 180) == (320, 180)           # smaller films are untouched
    w, h = video_quality.image_size(1920, 1080)
    assert abs(w / h - 16 / 9) < 0.01 and w % 2 == 0 and h % 2 == 0


def test_a_shot_really_comes_out_at_1080p_with_the_exact_frame_count(tmp_path):
    image = _picture(tmp_path / "p.png", 640, 360)
    out = animator.render_shot(animator.Shot(str(image), 1000, "push_in", frames=24),
                               tmp_path / "shot.mp4", 1920, 1080, 24)
    info = _ffprobe(out, "width,height,codec_name,pix_fmt,nb_read_frames")
    assert (info["width"], info["height"]) == ("1920", "1080")
    assert (info["codec_name"], info["pix_fmt"]) == ("h264", "yuv420p")
    assert info["nb_read_frames"] == "24"                # frame-exact, as the timeline needs


def test_the_delivered_pass_is_high_profile_with_the_index_up_front(tmp_path):
    clips = []
    for i in range(2):
        shot = animator.render_shot(
            animator.Shot(str(_picture(tmp_path / f"p{i}.png", 320, 180)), 1000, "drift", frames=24),
            tmp_path / f"s{i}.mp4", 320, 180, 24)
        clips.append(str(shot))
    out = tmp_path / "film.mp4"
    res = compositor_tool.CompositorTool().run(clips=clips, out_path=str(out),
                                               transition_ms=5 * 1000 / 24,      # exactly 5 frames
                                               durations_s=[1.0, 1.0], quality="final")
    assert res.success, res.error
    info = _ffprobe(out, "profile,pix_fmt,nb_read_frames")
    assert info["profile"] == "High" and info["pix_fmt"] == "yuv420p"
    data = out.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat"), "faststart: the web player can seek at once"
    assert info["nb_read_frames"] == str(24 * 2 - 5)     # two clips, one 5-frame crossfade


# ---- the enlargement comes first, with a good filter ------------------------------------

@pytest.mark.parametrize("width, height", [(1920, 1080), (1280, 720), (640, 360)])
def test_the_picture_is_enlarged_well_above_the_output_before_the_camera_moves(width, height):
    chain = camera.motion_filter("pan_right", 48, width, height, 24)
    first_scale, zoompan, last_scale = chain.index("scale=w="), chain.index("zoompan"), chain.rindex("scale=")
    assert first_scale < zoompan < last_scale
    working = int(re.search(r"scale=w=(\d+)", chain).group(1))
    assert working >= 2 * width * 0.99, "the move must be computed at about 2x the output or more"
    assert f"scale={width}:{height}:flags=lanczos" in chain.split("zoompan")[1]


def test_the_working_size_still_respects_the_memory_cap():
    cap = video_quality.get().upscale_max_pixels
    for width, height in ((1920, 1080), (1280, 720), (3840, 2160)):
        chain = camera.motion_filter("drift", 24, width, height, 24)
        w, h = (int(v) for v in re.search(r"scale=w=(\d+):h=(\d+)", chain).groups())
        assert w * h <= cap * 1.01, (width, height)


def test_the_enlargement_filter_is_the_configured_one(use_config):
    use_config(upscale__filter="spline")
    chain = camera.motion_filter("push_in", 24, 1920, 1080, 24)
    assert chain.count("flags=spline") == 2 and "lanczos" not in chain


def test_lanczos_enlarges_a_small_picture_with_crisper_edges_than_a_soft_filter(tmp_path, use_config):
    """Same picture, same move, same encode: only the enlargement filter differs."""
    image = _picture(tmp_path / "edges.png", 160, 90)
    energy = {}
    for name in ("lanczos", "bilinear"):
        use_config(upscale__filter=name, upscale__factor=2.0, sharpen__luma_amount=0)
        out = tmp_path / f"{name}.mp4"
        chain = camera.motion_filter("static_hold", 6, 640, 360, 24)
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(image), "-vf", chain + ",format=yuv420p",
                        "-frames:v", "6", "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-an",
                        str(out)], check=True, capture_output=True)
        f = _gray_frames(out, 640, 360)[2]
        lap = f[1:-1, 1:-1] * -4 + f[:-2, 1:-1] + f[2:, 1:-1] + f[1:-1, :-2] + f[1:-1, 2:]
        energy[name] = float(lap.var())
    assert energy["lanczos"] > energy["bilinear"] * 1.05, energy


# ---- the sharpen --------------------------------------------------------------------------

def test_the_light_sharpen_is_in_the_post_chain_once_and_before_the_grade(use_config):
    use_config(sharpen__luma_amount=0.4)
    chain = camera.post_chain("film noir", "", add_grain=True, add_vignette=True)
    assert chain.count("unsharp=") == 1 and "luma_amount=0.4" in chain and "chroma_amount=0" in chain
    assert chain.index("unsharp") < chain.index("vignette") < chain.index("noise")


def test_a_sharpen_of_zero_leaves_it_out(use_config):
    use_config(sharpen__luma_amount=0)
    assert "unsharp" not in camera.post_chain("", "")


# ---- eased motion -------------------------------------------------------------------------

def test_the_easing_is_smoothstep_or_linear_as_configured(use_config):
    use_config(motion__easing="smoothstep")
    assert camera.eased("p") == "((p)*(p)*(3-2*(p)))"
    use_config(motion__easing="linear")
    assert camera.eased("p") == "(p)"


def _line_positions(tmp_path, move, frames=40):
    """Where a thin bright line is in each frame of a pan: the pan's position over time."""
    image = _picture(tmp_path / f"{move}.png", 960, 540, kind="line")
    out = tmp_path / f"{move}.mp4"
    chain = camera.motion_filter(move, frames, 640, 360, 24, factor=1.5)
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(image), "-vf", chain + ",format=yuv420p",
                    "-frames:v", str(frames), "-c:v", "libx264", "-crf", "8", "-preset", "veryfast",
                    "-an", str(out)], check=True, capture_output=True)
    xs = []
    for f in _gray_frames(out, 640, 360):
        column = f.mean(axis=0)
        weight = np.clip(column - column.min() - 12, 0, None)
        xs.append(float((weight * np.arange(640)).sum() / max(weight.sum(), 1e-6)))
    return np.abs(np.diff(xs))


def test_a_pan_starts_and_ends_at_rest_and_is_fastest_in_the_middle(tmp_path, use_config):
    use_config(motion__easing="smoothstep")
    speed = _line_positions(tmp_path, "pan_right")
    ends, middle = np.r_[speed[:4], speed[-4:]].mean(), speed[len(speed) // 2 - 3: len(speed) // 2 + 3].mean()
    assert middle > 3 * ends, f"not eased: ends {ends:.2f} px/frame, middle {middle:.2f}"


def test_a_linear_pan_keeps_a_constant_speed(tmp_path, use_config):
    use_config(motion__easing="linear")
    speed = _line_positions(tmp_path, "pan_right")
    ends, middle = np.r_[speed[:4], speed[-4:]].mean(), speed[len(speed) // 2 - 3: len(speed) // 2 + 3].mean()
    assert 0.6 < middle / ends < 1.6, f"linear should not ease: ends {ends:.2f}, middle {middle:.2f}"


@pytest.mark.parametrize("move", sorted(camera.MOVES))
def test_every_move_still_renders_with_the_new_chain_at_1080p_proportions(move, tmp_path):
    out = animator.render_shot(
        animator.Shot(str(_picture(tmp_path / "p.png", 320, 180)), 500, move, frames=12),
        tmp_path / f"{move}.mp4", 960, 540, 24)
    assert _ffprobe(out, "nb_read_frames")["nb_read_frames"] == "12"
