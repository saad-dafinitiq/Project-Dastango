"""How a finished film looks, read from config/video.yaml (DST-35).

One place decides the picture size, the enlargement filter, the easing, the
sharpening and the x264 settings of every encode, so none of them is a number
buried in an ffmpeg command. Every value is checked when the file is read, and a
wrong one fails loudly naming itself, as config/monitoring.yaml does.

Two stages are encoded differently on purpose. A film used to be encoded up to
four times at the same low setting (a shot, a scene, the compose pass, the burned
subtitles), each pass losing a little more; now every pass but the last is near
transparent (`intermediate`) and only the last is the delivered quality (`final`).
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "video.yaml"

FILTERS = ("lanczos", "spline", "bicubic", "bilinear")
EASINGS = ("smoothstep", "linear")
PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower")
PROFILES = ("main", "high")
PIX_FMTS = ("yuv420p",)       # the one every browser and phone plays


@dataclass(frozen=True)
class VideoQuality:
    image_long_side: int
    upscale_filter: str
    upscale_factor: float
    upscale_max_pixels: int
    easing: str
    sharpen_amount: float
    intermediate_crf: int
    intermediate_preset: str
    final_crf: int
    final_preset: str
    final_profile: str
    pix_fmt: str
    grain_maxrate_kbps: int
    # How a scene is shot (DST-36).
    min_shots: int
    max_shots: int
    typical_shot_s: float
    min_shot_s: float
    cutaway_scene_s: float
    medium_crop: float
    handheld_zoom: float
    handheld_sway: float


def _section(data: dict, name: str, path: Path) -> dict:
    section = data.get(name)
    if not isinstance(section, dict):
        raise ValueError(f"{path.name}: section {name!r} is missing")
    return section


def _number(section: dict, name: str, key: str, path: Path, kind=float,
            low: float = 0, high: Optional[float] = None, inclusive_low: bool = False):
    raw = section.get(key)
    where = f"{name}.{key}"
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{path.name}: {where} must be a number, got {raw!r}")
    too_low = raw < low if inclusive_low else raw <= low
    if too_low or (high is not None and raw > high):
        bound = f"at least {low}" if inclusive_low else f"above {low}"
        raise ValueError(f"{path.name}: {where} must be {bound}"
                         + (f" and at most {high}" if high is not None else "") + f", got {raw}")
    return kind(raw)


def _choice(section: dict, name: str, key: str, allowed: Tuple[str, ...], path: Path) -> str:
    raw = section.get(key)
    if raw not in allowed:
        raise ValueError(f"{path.name}: {name}.{key} must be one of {', '.join(allowed)}, got {raw!r}")
    return raw


def load(path: Optional[Path | str] = None) -> VideoQuality:
    target = Path(path or os.getenv("VIDEO_CONFIG_FILE") or DEFAULT_CONFIG_PATH)
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    images, upscale = _section(data, "images", target), _section(data, "upscale", target)
    motion, sharpen = _section(data, "motion", target), _section(data, "sharpen", target)
    encode = _section(data, "encode", target)
    coverage, handheld = _section(data, "coverage", target), _section(data, "handheld", target)
    min_shots = _number(coverage, "coverage", "min_shots", target, int, low=1, inclusive_low=True,
                        high=8)
    max_shots = _number(coverage, "coverage", "max_shots", target, int, low=1, inclusive_low=True,
                        high=8)
    if max_shots < min_shots:
        raise ValueError(f"{target.name}: coverage.max_shots ({max_shots}) is below "
                         f"coverage.min_shots ({min_shots})")
    return VideoQuality(
        image_long_side=_number(images, "images", "long_side", target, int, low=319),
        upscale_filter=_choice(upscale, "upscale", "filter", FILTERS, target),
        upscale_factor=_number(upscale, "upscale", "factor", target, float, low=1, high=4,
                               inclusive_low=True),
        upscale_max_pixels=_number(upscale, "upscale", "max_pixels", target, int, low=1_000_000),
        easing=_choice(motion, "motion", "easing", EASINGS, target),
        sharpen_amount=_number(sharpen, "sharpen", "luma_amount", target, float, low=0, high=2,
                               inclusive_low=True),
        intermediate_crf=_number(encode, "encode", "intermediate_crf", target, int, low=0,
                                 high=30, inclusive_low=True),
        intermediate_preset=_choice(encode, "encode", "intermediate_preset", PRESETS, target),
        final_crf=_number(encode, "encode", "final_crf", target, int, low=0, high=30,
                          inclusive_low=True),
        final_preset=_choice(encode, "encode", "final_preset", PRESETS, target),
        final_profile=_choice(encode, "encode", "final_profile", PROFILES, target),
        pix_fmt=_choice(encode, "encode", "pix_fmt", PIX_FMTS, target),
        grain_maxrate_kbps=_number(encode, "encode", "grain_maxrate_kbps", target, int, low=1000,
                                   high=50000, inclusive_low=True),
        min_shots=min_shots,
        max_shots=max_shots,
        typical_shot_s=_number(coverage, "coverage", "typical_shot_s", target, float, low=1,
                               high=30, inclusive_low=True),
        min_shot_s=_number(coverage, "coverage", "min_shot_s", target, float, low=0.5, high=10,
                           inclusive_low=True),
        cutaway_scene_s=_number(coverage, "coverage", "cutaway_scene_s", target, float, low=1,
                                high=600, inclusive_low=True),
        medium_crop=_number(coverage, "coverage", "medium_crop", target, float, low=0.4, high=1,
                            inclusive_low=True),
        handheld_zoom=_number(handheld, "handheld", "zoom", target, float, low=1.01, high=1.3,
                              inclusive_low=True),
        handheld_sway=_number(handheld, "handheld", "sway", target, float, low=0, high=0.5,
                              inclusive_low=True),
    )


_cache: Dict[Tuple[str, int], VideoQuality] = {}


def get() -> VideoQuality:
    """The current settings. Re-read when the file (or VIDEO_CONFIG_FILE) changes."""
    path = Path(os.getenv("VIDEO_CONFIG_FILE") or DEFAULT_CONFIG_PATH)
    key = (str(path), path.stat().st_mtime_ns)
    if key not in _cache:
        _cache.clear()
        _cache[key] = load(path)
    return _cache[key]


def x264_args(stage: str) -> List[str]:
    """The video-encoder arguments for a pass: "intermediate" or "final".

    Every ffmpeg command that encodes video takes its arguments from here, so a
    changed setting reaches every stage and none keeps an old number of its own.
    """
    q = get()
    if stage == "intermediate":
        return ["-c:v", "libx264", "-preset", q.intermediate_preset,
                "-crf", str(q.intermediate_crf), "-pix_fmt", q.pix_fmt]
    if stage == "final":
        return ["-c:v", "libx264", "-preset", q.final_preset, "-crf", str(q.final_crf),
                "-profile:v", q.final_profile, "-pix_fmt", q.pix_fmt,
                "-movflags", "+faststart"]
    raise ValueError(f"unknown encode stage {stage!r}: use 'intermediate' or 'final'")


SQUARE_SIDE = 1024


def image_size(width: int, height: int) -> Tuple[int, int]:
    """The size the pictures of a film this big are drawn at.

    The camera enlarges every picture, so a picture is never stored bigger than
    `images.long_side` (a film smaller than that keeps its own size, as the test
    renders do). The aspect ratio is kept and both sides are even.
    """
    long_side = get().image_long_side
    biggest = max(width, height)
    if width == height:
        # Providers' native square is 1024: drawing 1080 or 1280 pixels of it only costs disk.
        side = min(biggest, SQUARE_SIDE)
        return side, side
    if biggest <= long_side:
        return width, height
    scale = long_side / biggest
    return max(2, int(round(width * scale / 2)) * 2), max(2, int(round(height * scale / 2)) * 2)
