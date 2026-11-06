"""Titles on the film (DST-39): a title card, scene titles, character name bars and an end card.

They are drawn as an animated ASS script that libass burns into the picture in the pass the
film is already given (shared with the subtitles when those are burned too), for three reasons:
libass shapes right-to-left and Indic text, which the installed Pillow cannot; text, fades and
moves stay vector-sharp at the film's real size; and nothing is added to the film's length, so
every graphic is an overlay on the timeline, never a new piece of it. Frame-exact sync is
therefore untouched by construction.

Where things sit comes from the format (a vertical film keeps clear of the apps' own interface),
how they look from the style lock (realistic: quiet, thin rule; anime: heavier outline and a
small pop). Only the two fonts the interface uses are involved, in assets/fonts/, unmodified.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from shared import film_format
from shared.fonts import font_for

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
TITLE_FONT = "Chillax Semibold"
BODY_FONT = "Synonym"
ACCENT = "&H003A8BD9&"        # #d98b3a, the interface's accent (ASS reads BGR)
WHITE = "&H00FFFFFF&"

TITLE_S, SCENE_TITLE_S, NAME_S, END_S = 3.2, 2.4, 3.0, 3.6
MAX_TEXT = 90


def _layout(name: Optional[str], w: int, h: int) -> Dict[str, int]:
    """Safe areas and type sizes in pixels. Fractions of the frame, per format, chosen to
    clear the burned-in subtitles and (on a vertical film) the apps' own buttons."""
    base = min(w, h)
    margin_x, top, above_subs = {
        "16:9": (0.06 * w, 0.08 * h, 0.33 * h),
        "9:16": (0.08 * w, 0.12 * h, 0.34 * h),
        "1:1": (0.08 * w, 0.08 * h, 0.31 * h),
    }[film_format.normalise_format(name)]
    return {
        "margin_x": round(margin_x), "top": round(top), "above_subs": round(above_subs),
        "title": round(0.085 * base), "tagline": round(0.034 * base),
        "scene": round(0.036 * base), "kicker": round(0.027 * base),
        "name": round(0.046 * base), "role": round(0.028 * base), "end": round(0.08 * base),
    }


def _ts(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _clean(text: str) -> str:
    """Text safe inside an ASS event: no override braces, no backslashes, no raw newlines."""
    text = re.sub(r"[{}\\]", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return (text[: MAX_TEXT - 1] + "…") if len(text) > MAX_TEXT else text


def _font(text: str, preferred: str, language: str) -> str:
    """The chosen face, unless it has none of the text's letters (Urdu, Hindi, CJK): then the
    same per-script face the subtitles use."""
    if any(ord(c) > 0x24F for c in text):
        return font_for(language)
    return preferred


def _rect(x: float, y: float, w: float, h: float) -> str:
    return f"m {x:.0f} {y:.0f} l {x + w:.0f} {y:.0f} {x + w:.0f} {y + h:.0f} {x:.0f} {y + h:.0f}"


def wanted(spec) -> bool:
    return bool(spec and (spec.title_card or spec.scene_titles or spec.lower_thirds or spec.end_card))


def build_ass(script, manifest, width: int, height: int, speed: float = 1.0,
              language: str = "English") -> Optional[str]:
    """The graphics script for this film, or None when none is switched on.

    Times come from the timeline (`manifest`), divided by the film's speed as the subtitles are,
    so a graphic is on the same scene whatever the pace.
    """
    spec = script.story.graphics
    if not wanted(spec):
        return None
    fmt = film_format.normalise_format(script.story.format)
    anime = script.story.style == "anime"
    lay = _layout(fmt, width, height)
    mx, w, h = lay["margin_x"], width, height

    def t(ms: float) -> float:
        return ms / 1000.0 / max(speed, 0.01)

    total = t(manifest.total_duration_ms)
    outline, shadow, spacing = (round(0.006 * min(w, h)), 0, 1) if anime else (0, 3, 3)
    rule_h = round(0.007 * min(w, h)) if anime else max(2, round(0.003 * min(w, h)))
    pop = r"\t(0,220,\fscx112\fscy112)\t(220,520,\fscx100\fscy100)" if anime else ""

    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{TITLE_FONT},{lay['title']},{WHITE},&H000000FF,&H001A1206&,&H78000000&,"
        f"0,0,0,0,100,100,{spacing},0,1,{outline},{shadow},5,{mx},{mx},0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    ev: List[str] = []

    def add(start: float, end: float, tags: str, text: str, layer: int = 1) -> None:
        if end - start >= 0.05:
            ev.append(f"Dialogue: {layer},{_ts(start)},{_ts(end)},Default,,0,0,0,,{{{tags}}}{text}")

    def scrim(start: float, end: float, alpha: int, fade_in: float, fade_out: float) -> None:
        # A full-frame black rectangle; `\1a` is its opacity (00 opaque, FF clear).
        add(start, end, rf"\an7\pos(0,0)\bord0\shad0\1c&H000000&\1a&H{255 - alpha:02X}&"
                        rf"\fad({int(fade_in * 1000)},{int(fade_out * 1000)})\p1",
            _rect(0, 0, w, h) + r"{\p0}", layer=0)

    scenes = list(manifest.scenes)
    first_len = t(scenes[0].duration_ms) if scenes else total

    # ---- title card: over the opening, never before it, so nothing moves on the timeline
    if spec.title_card:
        end = min(TITLE_S, max(1.2, first_len - 0.2), total)
        title = _clean(spec.title_text or script.story.title)
        logline = _clean(script.story.logline)
        cx, cy = w / 2, h / 2
        scrim(0, end, 130, 0.0, 0.7)
        face = _font(title, TITLE_FONT, language)
        add(0.15, end, rf"\an5\pos({cx:.0f},{cy - lay['title'] * 0.3:.0f})\fn{face}\fs{lay['title']}"
                       rf"\fad(600,700)\t(0,{end * 1000:.0f},\fscx104\fscy104){pop}", title, layer=2)
        rule_w = min(w - 2 * mx, round(0.16 * w))
        add(0.45, end, rf"\an5\pos({cx:.0f},{cy + lay['title'] * 0.55:.0f})\bord0\shad0\1c{ACCENT}"
                       rf"\fscx0\t(0,700,\fscx100)\fad(0,700)\p1",
            _rect(-rule_w / 2, 0, rule_w, rule_h) + r"{\p0}", layer=2)
        if logline:
            add(0.7, end, rf"\an8\pos({cx:.0f},{cy + lay['title'] * 0.8:.0f})\fn{_font(logline, BODY_FONT, language)}"
                          rf"\fs{lay['tagline']}\1c&H00E6E6E6&\fad(600,700)\q0", logline, layer=2)

    # ---- scene titles: top left, at the start of each scene
    if spec.scene_titles:
        for i, slot in enumerate(scenes):
            if i == 0 and spec.title_card:
                continue
            scene = next((s for s in script.scenes if s.scene_id == slot.scene_id), None)
            if not scene:
                continue
            start = t(slot.start_ms) + 0.25
            end = min(start + SCENE_TITLE_S, t(slot.end_ms) - 0.1)
            name = _clean(scene.title)
            face = _font(name, TITLE_FONT, language)
            add(start, end, rf"\an7\move({mx - 28},{lay['top']},{mx},{lay['top']},0,450)\fad(350,450)\fn{BODY_FONT}"
                            rf"\fs{lay['kicker']}\fsp5\1c{ACCENT}\bord{outline}\shad{shadow}",
                f"SCENE {i + 1}" + rf"\N{{\fn{face}\fs{lay['scene']}\fsp{spacing}\1c{WHITE}\bord{outline}}}{name}", layer=2)

    # ---- name bars: the first time each speaking character is heard
    if spec.lower_thirds:
        seen = set()
        people = {c.id: c for c in script.characters.characters}
        for seg in sorted((s for s in manifest.segments if s.kind == "dialogue"), key=lambda s: s.start_ms):
            c = people.get(seg.character_id or "")
            if not c or c.role == "narrator" or c.id in seen:
                continue
            seen.add(c.id)
            start = t(seg.start_ms) + 0.15
            end = min(start + NAME_S, total)
            y = h - lay["above_subs"]
            bar_h = round(lay["name"] * 1.25) + (round(lay["role"] * 1.3) if c.role in ("protagonist", "antagonist") else 0)
            nm = _clean(c.name)
            role = {"protagonist": "Protagonist", "antagonist": "Antagonist"}.get(c.role, "")
            add(start, end, rf"\an1\move({mx - 30},{y},{mx},{y},0,400)\bord0\shad0\1c{ACCENT}\fad(300,400)\p1",
                _rect(0, 0, rule_h * 2 + 2, bar_h) + r"{\p0}", layer=2)
            text = rf"{{\fn{_font(nm, TITLE_FONT, language)}\fs{lay['name']}\fsp{spacing}\bord{outline}}}{nm}"
            if role:
                text += rf"\N{{\fn{BODY_FONT}\fs{lay['role']}\fsp2\1c&H00D8D8D8&\bord0}}{role}"
            add(start, end, rf"\an1\move({mx + 22 - 30},{y - 4},{mx + 22},{y - 4},0,400)\fad(300,400){pop}\1c{WHITE}", text, layer=2)

    # ---- end card: over the closing seconds
    if spec.end_card and scenes:
        last = scenes[-1]
        start = max(t(last.start_ms), total - min(END_S, t(last.duration_ms)))
        text = _clean(spec.end_text or "The End")
        credits = _clean(spec.credits_text or "")
        cx, cy = w / 2, h / 2
        scrim(start, total, 190, 0.9, 0.0)
        add(start + 0.3, total, rf"\an5\pos({cx:.0f},{cy - (lay['end'] * 0.2 if credits else 0):.0f})"
                               rf"\fn{_font(text, TITLE_FONT, language)}\fs{lay['end']}\fad(700,0){pop}", text, layer=2)
        if credits:
            add(start + 0.7, total, rf"\an8\pos({cx:.0f},{cy + lay['end'] * 0.55:.0f})\fn{_font(credits, BODY_FONT, language)}"
                                    rf"\fs{lay['tagline']}\1c&H00E6E6E6&\fad(700,0)", credits, layer=2)

    return "\n".join(head + ev) + "\n" if ev else None


def filter_for(ass_path: Path) -> str:
    """The ffmpeg video filter that burns a graphics script in (fonts from assets/fonts)."""
    arg = lambda p: p.resolve().as_posix().replace(":", "\\:")
    return f"ass=filename='{arg(ass_path)}':fontsdir='{arg(FONTS_DIR)}'"
