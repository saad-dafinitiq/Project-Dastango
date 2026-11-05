"""Camera moves, and the grade that goes over them.

Pans used to be banned here. `zoompan` positions the crop window at integer
pixels, so a move of a fraction of a pixel per frame rounds inconsistently and
the picture shivers — measurably, not subjectively: a 1280x720 pan rendered the
old way wobbles with a standard deviation of 0.49 px and worst-case jumps of
0.63 px between frames, against a measurement floor of 0.000 on a locked-off
shot.

The fix is to do the move at several times the output size and scale down
afterwards, so a whole-pixel error upstream becomes a fraction of a pixel in
the delivered frame. Measured on the same shot:

    supersample   std dev   worst jump   render (3 s shot)
    1.6x (old)    0.494 px    0.631 px        1.1 s
    2x            0.361 px    0.583 px        1.5 s
    3x            0.312 px    0.397 px        2.8 s
    4x            0.242 px    0.365 px        5.1 s

3x is the default: everything is comfortably sub-pixel and the cost is about
1.7 s a shot. SUPERSAMPLE overrides it; the default, the pixel cap and the
enlargement filter are in config/video.yaml (DST-35). At 1080p the cap lowers
the factor to about 2x.

That enlargement is also where every picture is scaled up, once, with Lanczos:
the pictures are smaller than the film, and enlarging them inside zoompan would
stretch the low-resolution pixels. A move eases in and out (smoothstep) rather
than running at a constant speed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from shared import video_quality

# How far a pan travels, as a fraction of the frame. Beyond this the crop gets
# tight enough to show the image's own softness.
PAN_TRAVEL = 0.14
PUSH_RANGE = 0.16          # 1.00 -> 1.16 over the shot


def supersample() -> float:
    """How much bigger than the output the move is computed at: SUPERSAMPLE from
    the environment, else the factor in config/video.yaml."""
    default = video_quality.get().upscale_factor
    try:
        value = float(os.getenv("SUPERSAMPLE", "") or default)
    except ValueError:
        return default
    return min(4.0, max(1.0, value))


def supersample_for(width: int, height: int,
                    factor: Optional[float] = None) -> float:
    """The factor to actually use at this output size, within the pixel cap."""
    wanted = factor if factor is not None else supersample()
    pixels = max(1, width * height)
    allowed = (video_quality.get().upscale_max_pixels / pixels) ** 0.5
    return max(1.0, min(wanted, allowed))


def eased(progress: str) -> str:
    """An ffmpeg expression for how far a move has gone, from `progress` (0 to 1).

    smoothstep, 3p^2 - 2p^3, starts and ends at rest and is fastest in the middle.
    A constant speed (the old behaviour, still available as `linear`) starts and
    stops a move abruptly.
    """
    if video_quality.get().easing == "linear":
        return f"({progress})"
    return f"(({progress})*({progress})*(3-2*({progress})))"


@dataclass(frozen=True)
class Move:
    name: str
    description: str


MOVES: Dict[str, Move] = {
    "push_in": Move("push_in", "slow push towards the subject — builds pressure"),
    "pull_out": Move("pull_out", "pull back to reveal where we are"),
    "pan_left": Move("pan_left", "drift left across the frame"),
    "pan_right": Move("pan_right", "drift right across the frame"),
    "tilt_up": Move("tilt_up", "rise — scale, hope, the sky"),
    "tilt_down": Move("tilt_down", "settle downwards — weight, defeat"),
    "drift": Move("drift", "slow diagonal float, barely noticeable"),
    "static_hold": Move("static_hold", "locked off — lets a performance land"),
    "handheld": Move("handheld", "a slow sway, as if held — dialogue that breathes"),
}

# Which move suits which moment. These keys are the project's tone vocabulary
# (agents/story_agent/visual_style.TONE_HINTS); a test keeps the two in step,
# because a tone with no entry here silently falls back to a generic move.
TONE_MOVES: Dict[str, List[str]] = {
    "tense": ["push_in", "push_in", "static_hold"],
    "uneasy": ["push_in", "drift", "tilt_down"],
    "somber": ["pull_out", "tilt_down", "static_hold"],
    "melancholic": ["pull_out", "drift", "tilt_down"],
    "hopeful": ["tilt_up", "pull_out", "drift"],
    "joyful": ["pan_right", "tilt_up", "pull_out"],
    "wonder": ["pull_out", "tilt_up", "drift"],
    "curious": ["pan_left", "drift", "push_in"],
    "neutral": ["drift", "push_in", "static_hold"],
}

# A writing model will not restrict itself to that list, so near-misses map on
# rather than falling through to the generic default.
TONE_SYNONYMS: Dict[str, str] = {
    "anxious": "uneasy", "fearful": "uneasy", "afraid": "uneasy",
    "nervous": "uneasy", "ominous": "uneasy", "mysterious": "curious",
    "suspenseful": "tense", "urgent": "tense", "angry": "tense",
    "action": "tense", "dramatic": "tense",
    "sad": "melancholic", "grief": "melancholic", "wistful": "melancholic",
    "lonely": "melancholic", "bittersweet": "melancholic",
    "grim": "somber", "bleak": "somber", "solemn": "somber",
    "happy": "joyful", "triumphant": "joyful", "playful": "joyful",
    "warm": "joyful", "celebratory": "joyful",
    "awe": "wonder", "majestic": "wonder", "epic": "wonder",
    "calm": "neutral", "peaceful": "neutral", "quiet": "neutral",
    "reflective": "melancholic", "inquisitive": "curious",
}

ESTABLISHING_MOVES = ["pull_out", "pan_right", "drift", "tilt_up"]
# A face delivering a line: let the performance carry it.
DIALOGUE_MOVES = ["static_hold", "push_in", "static_hold"]


def canonical_tone(tone: str) -> str:
    """Map whatever the writer called it onto a tone with camera moves."""
    key = (tone or "").strip().lower()
    if key in TONE_MOVES:
        return key
    return TONE_SYNONYMS.get(key, "")


def move_for(shot_kind: str, tone: str = "", index: int = 0) -> str:
    """Pick a camera move that suits the shot and the moment.

    The video agent only ever makes three kinds of shot — `establishing` (a
    scene's opening and its narration), `character` and `lip_sync` — so tone
    has to act on those. An earlier version routed all three to fixed lists and
    only consulted the tone for kinds the agent never produced, which meant the
    scene's mood never reached the camera in a real render.

    Establishing shots carry the scene's feel. A face delivering a line holds
    still and lets the performance work — unless the moment is tense, when a
    slow push-in tightens on them.
    """
    kind = (shot_kind or "").lower()
    mood = canonical_tone(tone)
    if kind in ("lip_sync", "character", "dialogue"):
        options = ["push_in", "static_hold"] if mood in ("tense", "uneasy") else DIALOGUE_MOVES
    elif mood:
        options = TONE_MOVES[mood]
    elif kind in ("establishing", "wide"):
        options = ESTABLISHING_MOVES
    else:
        options = ["drift", "push_in", "static_hold", "pan_right"]
    return options[index % len(options)]


# Tones that make a line land heavily: the camera pushes in on them (DST-36).
HEAVY_TONES = ("tense", "uneasy", "somber", "melancholic")


def is_emotional(tone: str, text: str = "") -> bool:
    """Is this a moment for a slow push-in rather than a held, breathing frame?

    The scene's tone says so when it is a heavy one, and the line itself says so
    when it is shouted, trails off or breaks off ("!", "...", an all-capitals
    word). It is read from what the script already holds: no extra field of the
    writing model's answer is needed, and the same line always gets the same move.
    """
    if canonical_tone(tone) in HEAVY_TONES:
        return True
    words = [w.strip(".,!?\"'()") for w in (text or "").split()]
    return ("!" in text or "..." in text or "…" in text
            or any(len(w) >= 3 and w.isalpha() and w.isupper() for w in words))


def move_for_role(role: str, tone: str = "", emotional: bool = False,
                  scene_index: int = 0, shot_index: int = 0) -> str:
    """The camera move for a shot, from the job it does in its scene (DST-36).

    The job comes first, then the mood:
      ending        the scene's last shot pulls out, so each scene closes
      establishing  the scene's opening wide shot pans; alternate scenes pan
                    opposite ways so two in a row do not open the same way
      dialogue      a face delivering a line breathes (handheld), and a heavy
                    moment pushes in slowly
      narration     the world while a narrator speaks: a pan, the other way from the last
      cutaway       a detail, floating
    Every one of them is eased (motion_filter).
    """
    if role == "ending":
        return "pull_out"
    if role == "establishing":
        return "pan_right" if scene_index % 2 == 0 else "pan_left"   # shot_index is 0
    if role == "dialogue":
        return "push_in" if emotional else "handheld"
    if role == "narration":
        # The same alternation as the opening pan, so no two pans in a row go the same way.
        return "pan_right" if (scene_index + shot_index) % 2 == 0 else "pan_left"
    if role == "cutaway":
        return "drift"
    return "drift"


Crop = Tuple[float, float, float]     # (width as a fraction of the picture, x, y), x and y 0..1


def _handheld(frames: int, fps: int, seed: int) -> str:
    """zoompan for a handheld shot: two slow sines per axis, inside the headroom.

    The zoom leaves a margin (config handheld.zoom) and the sway uses a fraction of
    it (handheld.sway), so the picture's edge never shows. The frequencies, about
    0.45 and 1.1 Hz, are a breath and a slightly quicker tremor; their phases come
    from `seed`, so the shots of a film do not sway together.
    """
    q = video_quality.get()
    two_pi = 6.283185
    a1, a2 = two_pi * 0.45 / fps, two_pi * 1.1 / fps
    p = [((seed >> (6 * i)) % 63) / 10.0 for i in range(4)]   # four phases, 0 to 6.2
    k = q.handheld_sway
    x = (f"(iw-iw/zoom)*(0.5+{k:.3f}*(0.65*sin({a1:.5f}*on+{p[0]:.2f})"
         f"+0.35*sin({a2:.5f}*on+{p[1]:.2f})))")
    y = (f"(ih-ih/zoom)*(0.5+{k * 0.8:.3f}*(0.65*sin({a1 * 0.8:.5f}*on+{p[2]:.2f})"
         f"+0.35*sin({a2 * 0.9:.5f}*on+{p[3]:.2f})))")
    return f"zoompan=z={q.handheld_zoom:.3f}:x='{x}':y='{y}'"


def motion_filter(move: str, frames: int, width: int, height: int,
                  fps: int = 24, factor: Optional[float] = None,
                  crop: Optional[Crop] = None, seed: int = 0) -> str:
    """The ffmpeg filter chain for one camera move, supersampled.

    Returns the whole chain: [crop], scale up, move, scale back down. `crop` frames
    a part of the picture first (a medium shot out of the wide one, at no image
    cost); the one enlargement then happens on that part. `seed` varies the sway
    of a handheld shot, so two of them do not move in step.
    """
    f = max(1, frames)
    factor = supersample_for(width, height, factor)
    flt = video_quality.get().upscale_filter
    # Even dimensions: libx264 refuses an odd one.
    sw, sh = int(width * factor) // 2 * 2, int(height * factor) // 2 * 2

    # Everything is cropped out of a frame this much larger than the window,
    # which is what leaves room to travel.
    zoom = 1.0 / (1.0 - PAN_TRAVEL)
    held = f"{zoom:.5f}"
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"

    # `on` is the output frame index. How far the move has gone is eased, so it
    # starts and ends at rest instead of running at a constant speed.
    gone = eased(f"on/{max(1, f - 1)}")
    sweep = f"(iw-iw/zoom)*{gone}"
    sweep_back = f"(iw-iw/zoom)*(1-{gone})"
    rise = f"(ih-ih/zoom)*(1-{gone})"
    fall = f"(ih-ih/zoom)*{gone}"

    if move == "push_in":
        z = f"1+{PUSH_RANGE}*{gone}"
        inner = f"zoompan=z='{z}':x='{centre_x}':y='{centre_y}'"
    elif move == "pull_out":
        z = f"1+{PUSH_RANGE}*(1-{gone})"
        inner = f"zoompan=z='{z}':x='{centre_x}':y='{centre_y}'"
    elif move == "pan_right":
        inner = f"zoompan=z={held}:x='{sweep}':y='{centre_y}'"
    elif move == "pan_left":
        inner = f"zoompan=z={held}:x='{sweep_back}':y='{centre_y}'"
    elif move == "tilt_up":
        inner = f"zoompan=z={held}:x='{centre_x}':y='{rise}'"
    elif move == "tilt_down":
        inner = f"zoompan=z={held}:x='{centre_x}':y='{fall}'"
    elif move == "drift":
        # Diagonal, and only across a third of the available travel, so it
        # reads as a float rather than a move.
        inner = (f"zoompan=z={held}:x='(iw-iw/zoom)*(0.33+0.34*{gone})':"
                 f"y='(ih-ih/zoom)*(0.33+0.34*{gone})'")
    elif move == "handheld":
        inner = _handheld(f, fps, seed)
    else:   # static_hold and anything unrecognised
        inner = f"zoompan=z={held}:x='{centre_x}':y='{centre_y}'"

    framed = ""
    if crop and crop[0] < 1.0:
        w, px, py = crop
        # Even sides (libx264 refuses an odd one); the window keeps the picture's
        # own aspect, since both sides take the same fraction.
        framed = (f"crop=w=trunc(iw*{w:.4f}/2)*2:h=trunc(ih*{w:.4f}/2)*2:"
                  f"x=(iw-ow)*{px:.4f}:y=(ih-oh)*{py:.4f},")

    return (
        framed +
        # The one enlargement: the picture goes up to the working size here, with
        # the configured filter, before the camera touches it.
        f"scale=w={sw}:h={sh}:force_original_aspect_ratio=increase:flags={flt},"
        f"crop={sw}:{sh},"
        f"{inner}:d={f}:s={sw}x{sh}:fps={fps},"
        # Averaging the upstream whole-pixel step down to a fraction of an
        # output pixel is what makes pans usable.
        f"scale={width}:{height}:flags={flt}"
    )


# ---- grading -----------------------------------------------------------------

@dataclass(frozen=True)
class Grade:
    name: str
    filters: str


# Looks, keyed by what the story said it was. `visual_style` from phase 1 is
# matched against these, so the grade follows the film rather than a default.
GRADES: Dict[str, Grade] = {
    "teal_orange": Grade(
        "teal_orange",
        "colorbalance=rs=-0.06:bs=0.10:rm=0.04:bm=-0.02:rh=0.08:bh=-0.06,"
        "eq=contrast=1.10:saturation=1.10"),
    "cold": Grade(
        "cold",
        "colorbalance=rs=-0.08:bs=0.12:rm=-0.04:bm=0.06,"
        "eq=contrast=1.08:saturation=0.95"),
    "warm": Grade(
        "warm",
        "colorbalance=rs=0.08:bs=-0.06:rm=0.05:bm=-0.04,"
        "eq=contrast=1.05:saturation=1.12"),
    "noir": Grade(
        "noir",
        "eq=contrast=1.28:saturation=0.18:brightness=-0.02,"
        "curves=all='0/0 0.3/0.22 0.7/0.8 1/1'"),
    "bleach": Grade(
        "bleach",
        "eq=contrast=1.22:saturation=0.55,curves=all='0/0.03 0.5/0.52 1/0.97'"),
    "neutral": Grade("neutral", "eq=contrast=1.04:saturation=1.06"),
}

# Words in the story's own visual_style that imply a look.
_STYLE_WORDS = [
    (("noir", "monochrome", "black-and-white", "black and white"), "noir"),
    (("teal", "orange", "blockbuster", "anamorphic"), "teal_orange"),
    (("cold", "blue", "icy", "clinical", "sterile", "winter"), "cold"),
    (("warm", "golden", "amber", "sunlit", "nostalgic", "sepia"), "warm"),
    (("gritty", "bleak", "desaturated", "washed", "documentary"), "bleach"),
]
_TONE_GRADES = {
    "tense": "cold", "uneasy": "cold", "curious": "cold",
    "somber": "bleach", "melancholic": "bleach",
    "hopeful": "warm", "joyful": "warm", "wonder": "teal_orange",
}


def grade_for(visual_style: str = "", tone: str = "") -> Grade:
    """The colour treatment for a shot: the story's own look first, then tone."""
    text = (visual_style or "").lower()
    for words, name in _STYLE_WORDS:
        if any(word in text for word in words):
            return GRADES[name]
    return GRADES.get(_TONE_GRADES.get(canonical_tone(tone), ""), GRADES["neutral"])


def post_chain(visual_style: str = "", tone: str = "", add_grain: bool = True,
               add_vignette: bool = True, letterbox: bool = False,
               width: int = 0, height: int = 0) -> str:
    """Sharpen, vignette, grain, grade — the pass that makes stills look photographed."""
    parts: List[str] = []
    amount = video_quality.get().sharpen_amount
    if amount > 0:
        # Once, right after the last resize and before the encodes: enlarging a
        # small picture softens its edges, and this puts back the acuity (it can
        # not add detail the picture never had). Luma only, so colour does not ring.
        parts.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={amount:g}:"
                     f"chroma_msize_x=5:chroma_msize_y=5:chroma_amount=0")
    if add_vignette:
        parts.append("vignette=PI/5")
    if add_grain:
        # Spatial only. Temporal noise regenerates every frame and the eye
        # reads that as the whole picture shimmering.
        parts.append("noise=alls=2:allf=u")
    parts.append(grade_for(visual_style, tone).filters)
    if letterbox and width and height:
        bar = int(height * 0.055) // 2 * 2       # even, or libx264 complains
        if bar > 0:
            parts.append(f"drawbox=x=0:y=0:w={width}:h={bar}:color=black:t=fill")
            parts.append(f"drawbox=x=0:y={height - bar}:w={width}:h={bar}:"
                         f"color=black:t=fill")
    parts.append("format=yuv420p")
    return ",".join(parts)
