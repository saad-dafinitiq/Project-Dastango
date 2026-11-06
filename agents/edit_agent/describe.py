"""Say what an edit was understood as, in the words a creator would use.

Shown before any work starts, so a misread request is visible at once
("new pictures · scene 2" when they meant the music) rather than after a
minute of rendering the wrong thing.
"""
from __future__ import annotations
from typing import Any, Mapping

from agents.audio_agent.agent import TONE_ALIASES

# What each executor step is doing, for progress messages.
STEP_WORDS = {
    "rerun_audio": "Recording the voices again",
    "remix_audio": "Mixing the sound again",
    "set_music": "Choosing the music",
    "set_volume": "Setting the levels",
    "set_ambience": "Adding the ambience",
    "set_graphics": "Setting the titles",
    "set_effect": "Drawing the look over the scene",
    "regenerate_shot": "Choosing the shot to redraw",
    "redraw_shot": "Drawing the shot again",
    "edit_line": "Changing the line",
    "rerecord_line": "Recording the line again",
    "set_voice": "Choosing the voice",
    "remove_scene": "Taking the scene out",
    "move_scene": "Moving the scene",
    "restructure": "Laying the film out again",
    "apply_filter": "Regrading the pictures",
    "regenerate_scene": "Drawing the scene again",
    "regenerate_portraits": "Redesigning the characters",
    "recompose_video": "Cutting the film again",
    "change_speed": "Changing the pace",
    "regenerate_script": "Rewriting the script",
    "rerun_video": "Drawing the shots and cutting the film",
    "set_transition": "Setting the transition",
    "set_camera": "Setting the camera",
}


def _what(intent: Mapping[str, Any]) -> str:
    name = intent.get("intent", "")
    p = intent.get("parameters") or {}
    if name == "change_voice_tone":
        if not p.get("tone"):
            return "a different delivery"
        # The tone the voices will get, not the word the model used for it.
        tone = str(p["tone"]).strip().lower()
        return f"{TONE_ALIASES.get(tone, tone)} voices"
    if name == "change_voice":
        return "different voices"
    if name == "adjust_volume":
        return "louder voices" if float(p.get("volume", 1)) > 1 else "quieter voices"
    if name == "add_background_music":
        return f"{p.get('mood', 'new')} music"
    if name == "remove_background_music":
        return "no music"
    if name in ("apply_filter", "adjust_scene_aesthetic"):
        look = p.get("filter") or p.get("filter_name") or p.get("aesthetic") or "new"
        return f"a {str(look).replace('_', ' ')} look"
    if name == "regenerate_scene":
        return "new pictures"
    if name == "change_character_design":
        return "a new character design"
    if name in ("remove_subtitles", "add_subtitles"):
        return "subtitles " + ("off" if name == "remove_subtitles" else "on")
    if name in ("speed_up", "slow_down"):
        return "faster" if name == "speed_up" else "slower"
    if name in ("regenerate_script", "change_genre"):
        return f"a rewritten {p['genre']} script" if p.get("genre") else "a rewritten script"
    if name == "set_transition":
        if p.get("reset"):
            return "the default transition"
        from shared import transitions
        style = transitions.get(p["transition"]).label.lower() if p.get("transition") else "a transition"
        speed = p.get("speed") if p.get("speed") in ("quick", "slow") else ""
        return " ".join(w for w in (speed, style) if w)
    if name == "shot_camera":
        if p.get("reset"):
            return "the camera as planned"
        parts = [str(p["move"]).replace("_", " ")] if p.get("move") else []
        if p.get("framing"):
            parts.append(f"{p['framing']} framing")
        return ", ".join(parts) or "a different shot"
    if name == "set_music":
        if p.get("reset"):
            return "the music as it was made"
        if p.get("off"):
            return "no music"
        if p.get("track"):
            return f"the track {p['track']}"
        return f"{p.get('mood', 'new')} music"
    if name == "set_volume":
        return f"{p.get('target', 'the')} at {p.get('percent', '?')}%"
    if name == "set_effect":
        return "no effect" if p.get("effect") in (None, "none") else f"{p.get('strength', '')} {p['effect']}".strip()
    if name == "regenerate_shot":
        return f"shot {p.get('shot', '?')} drawn again"
    if name == "edit_line":
        return f"line {p.get('line', '?')} changed"
    if name == "remove_scene":
        return "the scene removed"
    if name == "move_scene":
        return f"the scene moved to place {p.get('to', '?')}"
    if name == "set_graphics":
        from agents.edit_agent.ops import GRAPHIC_WORDS
        if p.get("reset"):
            return "no titles"
        bits = [f"{'no ' if p[k] is False else ''}{GRAPHIC_WORDS[k]}" for k in GRAPHIC_WORDS if k in p]
        return ", ".join(bits) or "titles"
    if name == "set_ambience":
        if p.get("reset") or p.get("kind") == "none":
            return "no ambience"
        return f"{p.get('kind', 'some')} ambience"
    if name == "regenerate_audio":
        return "the voices recorded again"
    return name.replace("_", " ") or "a change"


def _where(scope: str, names: Mapping[str, str]) -> str:
    if scope.startswith("scene:"):
        return "scene " + scope.split(":", 1)[1].rsplit("_", 1)[-1]
    if scope.startswith("character:"):
        cid = scope.split(":", 1)[1]
        return names.get(cid) or cid.replace("char_", "")
    return "the whole film"


def describe(intent: Mapping[str, Any], names: Mapping[str, str] | None = None) -> str:
    """'whispered voices · scene 2', 'a noir look · the whole film'."""
    if intent.get("intent") == "set_transition":
        p = intent.get("parameters") or {}
        scene = (intent.get("scope") or "").rsplit("_", 1)[-1]
        shot = p.get("shot")
        where = (f"between scenes {int(scene) - 1} and {scene}" if scene.isdigit() and not shot or shot == 1
                 else f"scene {scene}, shot {shot}" if scene.isdigit() else "one cut")
        return f"{_what(intent)} · {where}"
    return f"{_what(intent)} · {_where(intent.get('scope') or 'global', names or {})}"
