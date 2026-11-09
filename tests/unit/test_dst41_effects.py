"""DST-41 (QA finding H2): film grain no longer costs 7 to 18 times the file, and vignette and cinema
bars no longer take 27 to 39 s.

Measured on the 17 s test film before this change: one scene went from 3.9 MB to 29, 49 and 69 MB for
soft, normal and strong grain (193 MB with grain on both scenes), and a version snapshot copies the
clips. Grain was drawn in Python and written at the film's own quality; the noise, new on every frame,
cannot be predicted by the encoder.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml

from shared import finish, video_quality

W, H, FPS = 1920, 1080, 24


def _clip(path: Path, seconds: float = 2.0, size: str = f"{W}x{H}") -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=duration={seconds}:size={size}:rate={FPS}", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", str(path)], check=True)
    return path


def _kbps(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    fmt = json.loads(out)["format"]
    return float(fmt["size"]) * 8 / 1000 / float(fmt["duration"])


def _gray(path: Path, frames: int = 6) -> np.ndarray:
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = (int(x) for x in size.split(","))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "format=gray", "-frames:v", str(frames),
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w).astype(np.float32)


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    return _clip(tmp_path_factory.mktemp("fx") / "plain.mp4")


# ---- grain: bounded size ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def long_clip(tmp_path_factory):
    return _clip(tmp_path_factory.mktemp("fxl") / "plain6.mp4", seconds=6.0)


@pytest.mark.parametrize("strength", ["soft", "normal", "strong"])
def test_grain_is_held_under_the_bitrate_ceiling_at_every_strength(long_clip, tmp_path, strength):
    """The ceiling is a rate-control target with a one-second buffer, so a short clip can run over it by
    the buffer's share of its length (a 2 s clip measured 9.9 Mb/s against 8 on the CI runner's x264, 7.5
    here). A real scene is 6 s or more; this one is 6 s and allowed a third over, which is the bound for
    that length and still far under the 25 to 40 Mb/s the same grain cost before the ceiling."""
    ceiling = video_quality.get().grain_maxrate_kbps
    out = finish.apply(long_clip, tmp_path / "g.mp4", "grain", strength, FPS)
    assert _kbps(out) <= ceiling * 1.3, f"{strength} grain was {_kbps(out):.0f} kb/s, ceiling {ceiling}"


def test_a_stronger_grain_is_visibly_stronger(clip, tmp_path):
    base = _gray(clip)
    std = {}
    for strength in ("soft", "normal", "strong"):
        got = _gray(finish.apply(clip, tmp_path / f"{strength}.mp4", "grain", strength, FPS))
        std[strength] = float(np.std(got - base))
    assert std["soft"] < std["normal"] < std["strong"], std
    assert std["soft"] > 0.8, std            # visible at all, not smoothed away by the encoder


def test_grain_is_new_on_every_frame_and_the_same_on_every_render(clip, tmp_path):
    a = _gray(finish.apply(clip, tmp_path / "a.mp4", "grain", "normal", FPS))
    b = _gray(finish.apply(clip, tmp_path / "b.mp4", "grain", "normal", FPS))
    base = _gray(clip)
    ra, rb = a - base, b - base                                   # the grain alone, in each render
    # The grain is seeded, so a re-render has the same grain: the two residuals agree. The encoder's rate
    # control is not bit-for-bit repeatable, and how far it differs depends on the build (0.95 here, 0.80 on the
    # CI runner), so the bar is "clearly the same noise": two different noise draws correlate at about 0.
    assert np.corrcoef(ra.ravel(), rb.ravel())[0, 1] > 0.5
    assert np.std(ra[1:] - ra[:-1]) > 0.5 * np.std(ra)            # and it moves: not a fixed pattern


def test_grain_keeps_the_frame_count_and_size(clip, tmp_path):
    out = finish.apply(clip, tmp_path / "g.mp4", "grain", "strong", FPS)
    assert finish._count_frames(out) == finish._count_frames(clip)
    assert finish._size(out) == (W, H)


# ---- the three effects ffmpeg has a recipe for no longer go through MoviePy -------------------

@pytest.mark.parametrize("kind", ["grain", "vignette", "letterbox"])
def test_these_effects_are_drawn_by_ffmpeg_not_by_moviepy(clip, tmp_path, monkeypatch, kind):
    def never(*_a, **_k):
        raise AssertionError("MoviePy was used")
    monkeypatch.setattr(finish, "_apply_moviepy", never)
    out = finish.apply(clip, tmp_path / f"{kind}.mp4", kind, "normal", FPS)
    assert finish._count_frames(out) == finish._count_frames(clip)


@pytest.mark.parametrize("kind", ["glow", "glitch"])
def test_glow_and_glitch_are_still_drawn_in_python(clip, tmp_path, monkeypatch, kind):
    used = []
    real = finish._apply_moviepy
    monkeypatch.setattr(finish, "_apply_moviepy", lambda *a, **k: (used.append(kind), real(*a, **k))[1])
    finish.apply(_clip(tmp_path / "small.mp4", 1.0, "320x180"), tmp_path / f"{kind}.mp4", kind, "normal", FPS)
    assert used == [kind]


# ---- the look of vignette and cinema bars is what it was --------------------------------------

def test_vignette_darkens_the_corners_and_leaves_the_middle(clip, tmp_path):
    base, got = _gray(clip, 3), _gray(finish.apply(clip, tmp_path / "v.mp4", "vignette", "normal", FPS), 3)
    corner = (slice(0, 60), slice(0, 100))
    middle = (slice(H // 2 - 50, H // 2 + 50), slice(W // 2 - 50, W // 2 + 50))
    assert got[0][corner].mean() < base[0][corner].mean() * 0.8          # the corner is clearly darker
    assert abs(got[0][middle].mean() - base[0][middle].mean()) < 2.0     # the middle is not touched


def test_vignette_matches_the_darkening_the_python_version_drew(clip, tmp_path):
    base, got = _gray(clip, 2), _gray(finish.apply(clip, tmp_path / "v.mp4", "vignette", "strong", FPS), 2)
    keep = finish._vignette_mask(W, H, finish.STRENGTHS["strong"])
    expected = base[0] * keep
    assert np.abs(got[0] - expected).mean() < 3.0                         # within the encoder's own noise


@pytest.mark.parametrize("size,bar", [("1920x1080", 138), ("1080x1920", 192), ("1080x1080", 108)])
def test_cinema_bars_cover_the_same_rows_as_before(tmp_path, size, bar):
    w, h = (int(x) for x in size.split("x"))
    assert finish._letterbox_bar(w, h) == bar
    out = finish.apply(_clip(tmp_path / "c.mp4", 1.0, size), tmp_path / "b.mp4", "letterbox", "normal", FPS)
    frame = _gray(out, 2)[0]
    assert frame[: bar - 2].max() <= 20 and frame[h - bar + 2:].max() <= 20          # black, to the codec's tolerance
    assert frame[bar + 4: h - bar - 4].mean() > 30                                 # the picture is still there


# ---- the ceiling is a checked setting ---------------------------------------------------------

def _config_with(tmp_path, **encode) -> Path:
    data = yaml.safe_load(video_quality.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["encode"].update(encode)
    path = tmp_path / "video.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_grain_ceiling_is_read_from_the_config_and_checked(tmp_path):
    assert video_quality.load(_config_with(tmp_path, grain_maxrate_kbps=5000)).grain_maxrate_kbps == 5000
    for bad in (100, 90000, "fast"):
        with pytest.raises(ValueError, match=re.escape("encode.grain_maxrate_kbps")):
            video_quality.load(_config_with(tmp_path, grain_maxrate_kbps=bad))
