"""The film's visual style — one look, derived from the story it is telling.

Every image prompt used to be forced through the same anime style, so a gritty
Mars thriller came back as a cheerful cel-shaded face. The style now comes from
the story: the LLM proposes one in `StoryOutput.visual_style`, and when it
doesn't (or the template wrote the script), the genre and tone pick a fitting
preset instead.

`VIDEO_STYLE` in .env overrides everything, for when you want one house look.
"""
from __future__ import annotations
import os
from typing import Optional, Tuple

from shared import film_format
from shared.schemas.story import StoryOutput

# genre -> (style, extra things to avoid)
GENRE_STYLES = {
    "sci-fi": ("cinematic science-fiction concept art, realistic materials, volumetric "
               "light, cold teal and amber palette, fine detail, 35mm depth of field",
               "cartoon, anime, chibi, cute, flat colors"),
    "horror": ("dark cinematic horror still, desaturated palette, heavy shadow, cold "
               "fog, film grain, unsettling composition",
               "cartoon, anime, cute, bright cheerful colors"),
    "mystery": ("moody noir cinematography, low-key lighting, rain-slick surfaces, "
                "muted palette with a single warm accent",
                "cartoon, anime, bright saturated colors"),
    "fantasy": ("painterly fantasy illustration, luminous colour, sweeping scale, "
                "detailed matte-painting background",
                "photograph, modern clothing, text"),
    "adventure": ("epic cinematic illustration, golden-hour light, wide vistas, "
                  "rich saturated colour",
                  "photograph, text, watermark"),
    "romance": ("soft warm cinematic photography, shallow depth of field, gentle "
                "backlight, pastel highlights",
                "horror, gore, harsh contrast"),
    "drama": ("naturalistic cinematic photography, soft daylight, muted colour, "
              "character-focused framing",
              "cartoon, anime, exaggerated"),
    "animation": ("anime, studio ghibli style, cel-shaded, vibrant saturated colours, "
                  "clean line art, painterly backgrounds",
                  "photograph, photorealistic, 3d render"),
}
DEFAULT_STYLE = GENRE_STYLES["drama"]

# Tone words that nudge the lighting, whatever the genre.
TONE_HINTS = {
    "tense": "tight framing, hard shadows",
    "uneasy": "off-balance framing, cool shadows",
    "somber": "overcast light, muted palette",
    "melancholic": "soft rain light, desaturated palette",
    "hopeful": "warm rim light, clearing sky",
    "joyful": "bright warm light, lively colour",
    "wonder": "luminous god rays, sense of scale",
    "curious": "soft directional light, inviting composition",
}

BASE_NEGATIVE = ("blurry, low quality, jpeg artifacts, deformed, extra limbs, "
                 "text, watermark, signature")
QUALITY = "masterpiece, highly detailed, consistent art direction"


def style_for(story: Optional[StoryOutput]) -> Tuple[str, str]:
    """(style, negative prompt) for this film: env override, the story's own
    style, or a genre preset. Without a story, the neutral default is used."""
    override = os.getenv("VIDEO_STYLE")
    if override:
        return f"{override}, {QUALITY}", BASE_NEGATIVE
    if story is None:
        return f"{DEFAULT_STYLE[0]}, {QUALITY}", f"{BASE_NEGATIVE}, {DEFAULT_STYLE[1]}"

    lock = film_format.style_lock(story.style)
    if lock:
        # The creator chose the medium: it comes first and is never overridden by the
        # story's own text, which keeps only what is left of it (palette, light, lens).
        palette = _palette_only(story.visual_style, lock["forbidden"])
        style = f"{lock['medium']}, {palette}" if palette else lock["medium"]
        return (f"{style}, {film_format.composition(story.format)}, {QUALITY}",
                f"{BASE_NEGATIVE}, {lock['avoid']}")

    genre = (story.genre or "").lower()
    preset = next((v for k, v in GENRE_STYLES.items() if k in genre), DEFAULT_STYLE)
    style = story.visual_style.strip() if story.visual_style else preset[0]
    negative = f"{BASE_NEGATIVE}, {preset[1]}"
    return f"{style}, {QUALITY}", negative


def _palette_only(text: str, forbidden: str) -> str:
    """The story's `visual_style` without any clause that names a medium the film is
    not in. A model told to write palette and lighting only will mostly do so; this is
    the guarantee for when it does not ("anime, painterly" in a realistic film)."""
    import re
    kept = [part.strip() for part in re.split(r"[,;]", text or "")
            if part.strip() and not re.search(forbidden, part, re.I)]
    return ", ".join(kept)


def scene_style(story: Optional[StoryOutput], tone: str = "") -> Tuple[str, str]:
    """Scene images: the film's look plus a nudge from this scene's tone."""
    style, negative = style_for(story)
    hint = TONE_HINTS.get((tone or "").lower())
    return (f"{style}, {hint}" if hint else style), negative


def portrait_style(story: Optional[StoryOutput]) -> Tuple[str, str]:
    """Character close-ups: the same look, framed as a portrait."""
    style, negative = style_for(story)
    return (f"{style}, character portrait, head and shoulders, expressive face, "
            f"looking at camera"), negative
