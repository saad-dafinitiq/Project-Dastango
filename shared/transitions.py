"""How one shot gives way to the next (DST-39).

A transition is a style and a length. Styles are ffmpeg `xfade` transitions: ffmpeg has about
forty and they cost nothing extra to render, which is why they, and not a Python compositor,
make the cut. This module is the one list of what a creator may choose, the defaults, and the
arithmetic that keeps a film exactly as long as its timeline whatever is chosen:

  A transition starts on the cut and runs past it, so the shot *before* the cut is rendered
  with `overlap_frames` extra frames (shared/timeline.py, `xfade_frames`). A straight cut has
  no overlap and is joined with `concat`, which is exact to the frame.

Pure and tested. The only I/O is one `ffmpeg -h filter=xfade` to learn which styles the
installed ffmpeg has, because the list grew between releases and the server's is not the
laptop's.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Set, Tuple

from shared.schemas.story import TransitionSpec
from shared.timeline import xfade_frames

MIN_MS = 100
MAX_MS = 1500

# Speeds the picker offers; any length between MIN_MS and MAX_MS is accepted from a sentence.
SPEEDS: Dict[str, int] = {"quick": 200, "normal": 400, "slow": 800}
NORMAL_MS = SPEEDS["normal"]

# Defaults (DST-39): cuts inside a scene, a soft fade between scenes. Films rendered before
# keep what they were cut with, a fade on every cut.
DEFAULT_WITHIN = TransitionSpec(kind="cut")
DEFAULT_BETWEEN = TransitionSpec(kind="fade", ms=400)
LEGACY_WITHIN = TransitionSpec(kind="fade", ms=200)
LEGACY_BETWEEN = TransitionSpec(kind="fade", ms=400)


@dataclass(frozen=True)
class Style:
    id: str
    label: str
    about: str
    xfade: Optional[str]      # ffmpeg's name; None for a straight cut
    main: bool = False        # offered first in the picker


# The zoom is not one of ffmpeg's xfade styles (its `zoomin` magnifies the last shot into a blur and
# a flat colour before the next one appears): shared/xfade.py makes it from a scale animation and a
# plain fade (DST-43). It needs nothing from ffmpeg but `fade`.
ZOOM = "zoomthrough"

STYLES: Tuple[Style, ...] = (
    Style("cut", "Cut", "Straight to the next shot. The default inside a scene.", None, True),
    Style("fade", "Soft fade", "One picture dissolves into the next. The default between scenes.", "fade", True),
    Style("fadeblack", "Dip to black", "Down to black and up again: a pause, a change of time.", "fadeblack", True),
    Style("wipe", "Wipe", "A line sweeps across and uncovers the next shot.", "wipeleft", True),
    Style("slide", "Slide", "The next shot pushes the last one out of the frame.", "slideleft", True),
    Style("zoom", "Zoom", "The camera pushes into the last shot and settles into the next.", ZOOM, True),
    Style("circleopen", "Circle open", "An opening circle reveals the next shot.", "circleopen"),
    Style("radial", "Radial", "A sweep like a clock hand uncovers the next shot.", "radial"),
    Style("dissolve", "Dissolve", "A grainy dissolve, rougher than the soft fade.", "dissolve"),
    Style("pixelize", "Pixelise", "The picture breaks into blocks and re-forms.", "pixelize"),
    Style("blur", "Blur", "The shot blurs sideways into the next.", "hblur"),
    Style("squeeze", "Squeeze", "The last shot squeezes away to show the next.", "squeezeh"),
    Style("diagonal", "Diagonal", "A diagonal wipe from the corner.", "diagtl"),
    Style("slice", "Slice", "Slices of the next shot slide in.", "hlslice"),
    Style("cover", "Cover", "The next shot slides over the last.", "coverleft"),
    Style("reveal", "Reveal", "The last shot slides away off the next.", "revealleft"),
)
_BY_ID: Dict[str, Style] = {s.id: s for s in STYLES}

# What people (and models) call them, to ids.
ALIASES: Dict[str, str] = {
    "hard cut": "cut", "straight cut": "cut", "no transition": "cut", "none": "cut",
    "crossfade": "fade", "cross fade": "fade", "cross-fade": "fade", "soft fade": "fade",
    "dissolve": "dissolve", "fade to black": "fadeblack", "dip to black": "fadeblack",
    "dip": "fadeblack", "black": "fadeblack", "wipe": "wipe", "slide": "slide", "push": "slide",
    "zoom": "zoom", "zoom in": "zoom", "circle": "circleopen", "pixelise": "pixelize",
    "pixelate": "pixelize", "blur": "blur", "squeeze": "squeeze", "diagonal": "diagonal",
    "slice": "slice", "cover": "cover", "reveal": "reveal", "radial": "radial", "fade": "fade",
}


@lru_cache(maxsize=1)
def available_xfades() -> Set[str]:
    """The xfade transitions the installed ffmpeg has. Without ffmpeg, only the plain fade is
    assumed, so nothing here can ask for something the render cannot do."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"], capture_output=True,
                             text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return {"fade", ZOOM}
    names = set(re.findall(r"^\s{4,}([a-z]+)\s", out, re.M))
    return (names or {"fade"}) | {ZOOM}


def styles() -> List[Style]:
    """The styles a creator can choose on this machine, the main six first."""
    have = available_xfades()
    usable = [s for s in STYLES if s.xfade is None or s.xfade in have]
    return sorted(usable, key=lambda s: not s.main)       # stable: keeps the table's order inside


def get(kind: str) -> Style:
    s = _BY_ID.get((kind or "").strip().lower())
    if s is None:
        raise ValueError(f"unknown transition {kind!r}: use one of {', '.join(x.id for x in styles())}")
    if s.xfade is not None and s.xfade not in available_xfades():
        raise ValueError(f"this server's ffmpeg has no {s.label.lower()} transition: "
                         f"use one of {', '.join(x.id for x in styles())}")
    return s


def from_words(text: str) -> Optional[str]:
    """A style id from what someone wrote ('dip to black', 'crossfade'), longest name first."""
    t = (text or "").lower()
    for name in sorted(ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", t):
            return ALIASES[name]
    return None


def speed_ms(speed: Optional[str] = None, ms: Optional[int] = None) -> Optional[int]:
    """A length in ms from a named speed or an explicit number, checked. None = the normal length."""
    if ms is not None:
        if not MIN_MS <= int(ms) <= MAX_MS:
            raise ValueError(f"a transition takes {MIN_MS} to {MAX_MS} ms, not {ms}")
        return int(ms)
    if speed:
        if speed not in SPEEDS:
            raise ValueError(f"unknown speed {speed!r}: use one of {', '.join(SPEEDS)}")
        return SPEEDS[speed]
    return None


def default_for(between_scenes: bool, legacy: bool = False) -> TransitionSpec:
    if legacy:
        return LEGACY_BETWEEN if between_scenes else LEGACY_WITHIN
    return DEFAULT_BETWEEN if between_scenes else DEFAULT_WITHIN


def resolve(spec: Optional[TransitionSpec], between_scenes: bool, legacy: bool = False) -> Tuple[str, int]:
    """(style id, milliseconds) of a cut: the creator's choice or the default. A cut is 0 ms."""
    spec = spec or default_for(between_scenes, legacy)
    if spec.kind == "cut":
        return "cut", 0
    return spec.kind, spec.ms or NORMAL_MS


def overlap_frames(kind: str, ms: int, fps: int, before_frames: int, after_frames: int) -> int:
    """Frames of overlap for a cut. Never more than half of either shot, so a transition
    cannot swallow the shot it joins; a cut, or a shot too short to share, is 0."""
    if kind == "cut" or ms <= 0:
        return 0
    return max(0, min(xfade_frames(ms, fps), before_frames // 2, after_frames // 2))


def xfade_name(kind: str) -> Optional[str]:
    """ffmpeg's name for a style, or None for a cut. Falls back to the plain fade if this
    ffmpeg lacks the style (a film saved on a newer server and edited on an older one)."""
    s = _BY_ID.get(kind)
    if s is None or s.xfade is None:
        return None
    return s.xfade if s.xfade in available_xfades() else "fade"
