"""Finishing effects for a scene's clip (DST-39), done with MoviePy.

ffmpeg's filters are fixed recipes; these are drawn frame by frame in Python (numpy), so a look
can depend on the frame number: a glitch that pulses on a beat, grain that is different every
frame yet the same on every render, a glow built from the picture's own highlights. Each effect
changes pixels only, never the number of frames, so the film stays exactly the timeline's length
(checked after every write, and put right if a codec ever disagrees).

Text is not done here: libass draws titles (shared/graphics.py).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np

from shared import video_quality

EFFECTS: Dict[str, Dict[str, str]] = {
    "vignette": {"label": "Vignette", "about": "darkened corners that draw the eye in"},
    "grain": {"label": "Film grain", "about": "a fine, live grain like real film"},
    "glow": {"label": "Glow", "about": "highlights bloom softly into the shadows"},
    "letterbox": {"label": "Cinema bars", "about": "black bars above and below, as in a cinema"},
    "glitch": {"label": "Glitch", "about": "colour splits that flicker now and then"},
}
STRENGTHS = {"soft": 0.55, "normal": 1.0, "strong": 1.6}


def _vignette(w: int, h: int, level: float, fps: int):
    y, x = np.ogrid[:h, :w]
    d = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2)
    mask = (1.0 - np.clip((d - 0.45) / 0.9, 0, 1) ** 1.6 * min(0.8, 0.55 * level)).astype(np.float32)[..., None]
    return lambda f, i: (f * mask).astype(np.uint8)


def _grain(w: int, h: int, level: float, fps: int):
    sigma = 9.0 * level

    def fn(f, i):
        # Seeded by the frame, so a re-render of the same scene is the same grain.
        noise = np.random.default_rng(1000 + i).normal(0.0, sigma, (h, w, 1)).astype(np.float32)
        return np.clip(f.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return fn


def _glow(w: int, h: int, level: float, fps: int):
    from PIL import Image, ImageFilter
    small = (max(2, w // 4), max(2, h // 4))
    radius = max(2.0, h / 90)

    def fn(f, i):
        bright = np.clip(f.astype(np.float32) - 150.0, 0, None) * (255.0 / 105.0)
        img = Image.fromarray(np.clip(bright, 0, 255).astype(np.uint8)).resize(small)
        blur = img.filter(ImageFilter.GaussianBlur(radius)).resize((w, h), Image.BILINEAR)
        out = f.astype(np.float32) + np.asarray(blur, dtype=np.float32) * (0.55 * level)
        return np.clip(out, 0, 255).astype(np.uint8)
    return fn


def _letterbox(w: int, h: int, level: float, fps: int):
    # A 2.39:1 picture inside the frame; on a square or tall frame a fixed tenth, since 2.39:1
    # there would leave almost nothing.
    bar = int(round((h - w / 2.39) / 2)) if w / h > 1.6 else int(round(h * 0.10))
    bar = max(2, min(bar, h // 4))

    def fn(f, i):
        out = f.copy()
        out[:bar] = 0
        out[h - bar:] = 0
        return out
    return fn


def _glitch(w: int, h: int, level: float, fps: int):
    period, burst = max(8, int(fps * 1.6)), max(3, fps // 4)
    dx = max(2, int(w * 0.006 * level))

    def fn(f, i):
        k = i % period
        if i < fps or k >= burst:
            return f
        out = f.copy()
        s = dx * (1 + (k % 3))
        out[:, :, 0] = np.roll(f[:, :, 0], s, axis=1)
        out[:, :, 2] = np.roll(f[:, :, 2], -s, axis=1)
        band = np.random.default_rng(i).integers(0, max(1, h - h // 8))
        out[band: band + h // 14] = np.roll(out[band: band + h // 14], s * 3, axis=1)
        return out
    return fn


_BUILD: Dict[str, Callable] = {"vignette": _vignette, "grain": _grain, "glow": _glow,
                               "letterbox": _letterbox, "glitch": _glitch}


def _count_frames(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def apply(src: Path, dst: Path, kind: str, strength: str, fps: int) -> Path:
    """`src` with the effect drawn over every frame, written to `dst` with the same frame count."""
    from moviepy import VideoFileClip

    if kind not in _BUILD:
        raise ValueError(f"unknown effect {kind!r}: use one of {', '.join(EFFECTS)}")
    level = STRENGTHS.get(strength, 1.0)
    clip = VideoFileClip(str(src), audio=False)
    try:
        w, h = clip.size
        fn = _BUILD[kind](w, h, level, fps)
        out = clip.transform(lambda get_frame, t: fn(get_frame(t), int(round(t * fps))))
        # The encoder settings are the pipeline's own (config/video.yaml), minus the codec flag
        # MoviePy takes as an argument.
        args = video_quality.x264_args("intermediate")
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.write_videofile(str(dst), fps=fps, codec="libx264", audio=False, logger=None,
                            ffmpeg_params=args[2:])
    finally:
        clip.close()
    want, got = _count_frames(src), _count_frames(dst)
    if want != got:
        # A codec's rounding at the end of a clip: make the count exact rather than let sync drift.
        fixed = dst.with_name(dst.stem + "_fit.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dst), "-vf",
                        f"tpad=stop_mode=clone:stop={max(0, want - got)}", "-frames:v", str(want),
                        *video_quality.x264_args("intermediate"), str(fixed)], check=True)
        fixed.replace(dst)
    return dst
