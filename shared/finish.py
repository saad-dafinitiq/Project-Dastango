"""Finishing effects for a scene's clip (DST-39): ffmpeg where it has a recipe, MoviePy where not.

Grain, vignette and cinema bars are ffmpeg filters (DST-41): a recipe ffmpeg has costs a few
seconds, where drawing the same frame by frame in Python took 27 to 64 s for one scene and, for
grain, wrote a file 7 to 18 times the size. A glow built from the picture's own highlights and a
glitch that pulses on a beat are drawn frame by frame in Python (numpy, through MoviePy), so a look
can depend on the frame number. Each effect changes pixels only, never the number of frames, so the
film stays exactly the timeline's length (checked after every write, and put right if a codec ever
disagrees).

Text is not done here: libass draws titles (shared/graphics.py).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np

from shared import progress_hook, video_quality
from shared import procs

EFFECTS: Dict[str, Dict[str, str]] = {
    "vignette": {"label": "Vignette", "about": "darkened corners that draw the eye in"},
    "grain": {"label": "Film grain", "about": "a fine, live grain like real film"},
    "glow": {"label": "Glow", "about": "highlights bloom softly into the shadows"},
    "letterbox": {"label": "Cinema bars", "about": "black bars above and below, as in a cinema"},
    "glitch": {"label": "Glitch", "about": "colour splits that flicker now and then"},
}
STRENGTHS = {"soft": 0.55, "normal": 1.0, "strong": 1.6}


# Film grain: the noise filter's strength at "normal" (the scale is 0 to 100; the others follow
# STRENGTHS), uniform noise drawn anew on every frame, with a fixed seed so a re-render of the same
# scene has the same grain. Chosen with the encoder's ceiling (video.yaml, encode.grain_maxrate_kbps).
GRAIN_STRENGTH = 9
GRAIN_SEED = 1000
FFMPEG_EFFECTS = ("grain", "vignette", "letterbox")


def _vignette_mask(w: int, h: int, level: float) -> np.ndarray:
    """How much of the picture is kept at each pixel: 1 in the middle, less toward the corners."""
    y, x = np.ogrid[:h, :w]
    d = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2)
    return (1.0 - np.clip((d - 0.45) / 0.9, 0, 1) ** 1.6 * min(0.8, 0.55 * level)).astype(np.float32)


def _letterbox_bar(w: int, h: int) -> int:
    # A 2.39:1 picture inside the frame; on a square or tall frame a fixed tenth, since 2.39:1
    # there would leave almost nothing.
    bar = int(round((h - w / 2.39) / 2)) if w / h > 1.6 else int(round(h * 0.10))
    return max(2, min(bar, h // 4))


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


_BUILD: Dict[str, Callable] = {"glow": _glow, "glitch": _glitch}      # drawn in Python, frame by frame


def _count_frames(path: Path) -> int:
    out = procs.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def apply(src: Path, dst: Path, kind: str, strength: str, fps: int) -> Path:
    """`src` with the effect drawn over every frame, written to `dst` with the same frame count."""
    if kind not in _BUILD and kind not in FFMPEG_EFFECTS:
        raise ValueError(f"unknown effect {kind!r}: use one of {', '.join(EFFECTS)}")
    level = STRENGTHS.get(strength, 1.0)
    if kind in FFMPEG_EFFECTS:
        _apply_ffmpeg(src, dst, kind, level)
    else:
        _apply_moviepy(src, dst, kind, level, fps)
    want, got = _count_frames(src), _count_frames(dst)
    if want != got:
        # A codec's rounding at the end of a clip: make the count exact rather than let sync drift.
        fixed = dst.with_name(dst.stem + "_fit.mp4")
        procs.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dst), "-vf",
                        f"tpad=stop_mode=clone:stop={max(0, want - got)}", "-frames:v", str(want),
                        *video_quality.x264_args("intermediate"), str(fixed)], check=True)
        fixed.replace(dst)
    return dst


def _size(src: Path) -> tuple:
    out = procs.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(src)],
                         capture_output=True, text=True, check=True).stdout
    stream = json.loads(out)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _apply_ffmpeg(src: Path, dst: Path, kind: str, level: float) -> None:
    """Grain, vignette and cinema bars as ffmpeg filters, encoded like any intermediate clip
    (a grained one under the bitrate ceiling in config/video.yaml)."""
    w, h = _size(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    encode = list(video_quality.x264_args("intermediate"))
    mask = None
    if kind == "grain":
        strength = max(1, min(100, int(round(GRAIN_STRENGTH * level))))
        graph = ["-vf", f"noise=c0s={strength}:c0f=t+u:c0_seed={GRAIN_SEED}"]
        ceiling = video_quality.get().grain_maxrate_kbps
        # A buffer of one second at the ceiling: a longer one lets a short clip burst well past it.
        # (A rate-limited x264 encode is not bit-for-bit repeatable between runs, because its rate
        # control follows its threads' timing; the grain itself is, being seeded, so a re-render is
        # the same grain.)
        encode += ["-maxrate", f"{ceiling}k", "-bufsize", f"{ceiling}k"]
        inputs = ["-i", str(src)]
    elif kind == "letterbox":
        bar = _letterbox_bar(w, h)
        graph = ["-vf", f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,"
                        f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill"]
        inputs = ["-i", str(src)]
    else:   # vignette: the same darkening the Python version drew, laid over as a black picture whose
        #     opacity is how much of the frame is taken away at each pixel
        from PIL import Image
        keep = _vignette_mask(w, h, level)
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., 3] = np.round((1.0 - keep) * 255.0).astype(np.uint8)
        mask = dst.with_name(dst.stem + "_mask.png")
        Image.fromarray(rgba).save(mask)          # four channels: RGBA
        inputs = ["-i", str(src), "-loop", "1", "-i", str(mask)]
        graph = ["-filter_complex", "[0:v][1:v]overlay=shortest=1:format=auto,format=yuv420p[v]", "-map", "[v]"]
    try:
        procs.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, *graph, *encode, "-an", str(dst)],
                       check=True, capture_output=True)
    finally:
        if mask is not None:
            mask.unlink(missing_ok=True)


def _apply_moviepy(src: Path, dst: Path, kind: str, level: float, fps: int) -> None:
    """Glow and glitch, drawn frame by frame in Python."""
    from moviepy import VideoFileClip

    clip = VideoFileClip(str(src), audio=False)
    try:
        w, h = clip.size
        fn = _BUILD[kind](w, h, level, fps)
        def draw(get_frame, t):
            progress_hook.pulse()           # lets a Stop land between frames (DST-41)
            return fn(get_frame(t), int(round(t * fps)))

        out = clip.transform(draw)
        # The encoder settings are the pipeline's own (config/video.yaml), minus the codec flag
        # MoviePy takes as an argument.
        args = video_quality.x264_args("intermediate")
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.write_videofile(str(dst), fps=fps, codec="libx264", audio=False, logger=None,
                            ffmpeg_params=args[2:])
    finally:
        clip.close()
