"""The film's format and look, chosen before planning (DST-34).

Two choices travel with the script (`StoryOutput.format` and `.style`) and every
agent reads them from here, so nothing downstream assumes a 16:9 realistic-or-whatever
film. Pure data and arithmetic: no I/O, so it is tested exactly.

Format: 16:9 (YouTube), 9:16 (Shorts, Reels, TikTok), 1:1 (Instagram feed).
Style: realistic or anime. A film made before this existed has style "" and keeps
the look the story's genre gave it (agents/story_agent/visual_style.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

DEFAULT_FORMAT = "16:9"
DEFAULT_STYLE = "realistic"


@dataclass(frozen=True)
class Format:
    name: str
    label: str          # what a creator calls it
    audience: str       # where it is meant to be posted
    film: Tuple[int, int]   # the finished film's size
    # How the burned-in subtitles sit (fractions of the frame; see `subtitle_layout`).
    font_h: float       # font height as a fraction of the frame height
    margin_v: float     # bottom margin: clears the apps' own interface on vertical video
    margin_h: float     # left and right margin, each side, as a fraction of the width
    composition: str    # added to every picture prompt
    brief: str          # said to the story model so it writes scenes that fit the frame


FORMATS: Dict[str, Format] = {
    "16:9": Format(
        "16:9", "Widescreen", "YouTube", (1920, 1080),
        # 22/288, 40/288 and 80/384: what the old fixed values came to on libass's 384 x 288 grid,
        # so a 16:9 film's subtitles look as they always did.
        font_h=22 / 288, margin_v=40 / 288, margin_h=80 / 384,
        composition="wide cinematic 16:9 composition",
        brief="a wide 16:9 frame: landscapes, groups and rooms read well"),
    "9:16": Format(
        "9:16", "Vertical", "Shorts, Reels, TikTok", (1080, 1920),
        # The bottom fifth of a vertical video sits under the captions, buttons and
        # account line of Shorts, Reels and TikTok, so subtitles are held above it.
        font_h=0.040, margin_v=0.22, margin_h=0.10,
        composition="vertical 9:16 portrait composition, subject centred, tall framing",
        brief="a tall 9:16 phone frame: one or two figures filling the height, "
              "not wide landscapes or groups side by side"),
    "1:1": Format(
        "1:1", "Square", "Instagram feed", (1080, 1080),
        font_h=0.060, margin_v=0.12, margin_h=0.12,
        composition="square 1:1 composition, subject centred",
        brief="a square frame: a centred subject with room around it"),
}

# The medium of each style, said first in every picture prompt, and what it must never
# look like. Written as plain sentences because the providers' models differ (some read a
# negative prompt, the free FLUX endpoint reads none), so the lock has to work in the
# positive text alone and is only reinforced by the negative where one is read.
STYLES: Dict[str, Dict[str, str]] = {
    "realistic": {
        "label": "Realistic",
        "about": "Photographic, natural light",
        "medium": ("a live-action cinematic photograph, photorealistic, natural lighting, real materials "
                   "and skin, 35mm lens, shallow depth of field, not an illustration, not anime, not a cartoon"),
        "avoid": "anime, manga, cartoon, illustration, cel-shaded, painting, 3d render, cgi, flat colors",
        # Words that would pull a story's own palette text out of the medium.
        "forbidden": r"anime|manga|ghibli|cartoon|cel[- ]?shad|illustrat|paint(?:ed|erly|ing)|concept art|3d render|cgi|line art",
    },
    "anime": {
        "label": "Anime",
        "about": "Painted, cel-shaded",
        "medium": ("an anime film still, hand-drawn cel animation, clean line art, painted backgrounds, "
                   "expressive colour, not a photograph, not photorealistic, not live action"),
        "avoid": "photograph, photorealistic, live action, 3d render, cgi, realistic skin texture",
        "forbidden": r"photo(?:graph|realis)\w*|live[- ]action|35mm|film grain|cinematography|3d render|cgi|realistic",
    },
}


def normalise_format(value: Optional[str]) -> str:
    """A format name, or the default. Accepts "16x9" and "9 : 16" as people write them."""
    v = (value or "").strip().replace("x", ":").replace(" ", "")
    return v if v in FORMATS else DEFAULT_FORMAT


def check_format(value: str) -> str:
    """The format, or ValueError naming what is accepted (an API refuses with this)."""
    v = (value or "").strip()
    if v not in FORMATS:
        raise ValueError(f"unknown format {value!r}: use one of {', '.join(FORMATS)}")
    return v


def check_style(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in STYLES:
        raise ValueError(f"unknown style {value!r}: use one of {', '.join(STYLES)}")
    return v


def get(name: Optional[str]) -> Format:
    return FORMATS[normalise_format(name)]


def film_size(name: Optional[str]) -> Tuple[int, int]:
    """The finished film's size in pixels."""
    return get(name).film


def format_of_size(width: int, height: int) -> str:
    """The format a film of this size is in: the nearest, for films made before the
    format was recorded."""
    ratio = width / max(1, height)
    return min(FORMATS, key=lambda n: abs(FORMATS[n].film[0] / FORMATS[n].film[1] - ratio))


def subtitle_layout(name: Optional[str], width: int, height: int) -> Dict[str, int]:
    """Where burned-in subtitles go in a frame of `width` x `height`, in pixels.

    libass reads sizes against a script grid of its own (384 x 288 for an SRT) and
    scales them to the video, so a font size and margins tuned for a 16:9 film come
    out far too large and too far in on a tall one. The layout is therefore worked out
    from the frame's pixels and the filter is told the frame's size (`original_size`),
    which makes one ASS unit one pixel.
    """
    f = get(name)
    return {
        "font_px": max(10, int(round(height * f.font_h))),
        "margin_v": int(round(height * f.margin_v)),
        "margin_l": int(round(width * f.margin_h)),
        "margin_r": int(round(width * f.margin_h)),
        "outline": max(1, int(round(height * f.font_h / 11))),
    }


def composition(name: Optional[str]) -> str:
    return get(name).composition


def style_lock(style: Optional[str]) -> Optional[Dict[str, str]]:
    """The lock for a style, or None for a film with no style (made before DST-34)."""
    return STYLES.get((style or "").strip().lower())
