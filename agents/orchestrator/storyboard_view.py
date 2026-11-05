"""What a storyboard card shows, in one place.

The creator interface draws a strip of scene cards. The same cards appear two
ways — streamed as progress events while a plan is still being drawn, and read
back from the storyboard endpoint when the page is reloaded — so they are built
here once, and both paths agree by construction rather than by care.

Everything on a card is real state: the scene's tone and the camera move that
tone will produce, and for each line the character and the voice that will
speak it. None of it is decoration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from shared import providers, voices
from shared.assets import asset_url

from agents.story_agent.cast import join_names
from agents.video_agent import coverage


def _voice_label(engine: str, voice_id: str) -> str:
    """'bm_george' -> 'George'. The voice tables carry a human label."""
    entry = voices.get(engine)
    if entry:
        for v in entry.voices:
            if v.id == voice_id and v.label:
                return v.label.split(" · ")[0].strip()
    return voice_id or ""


def shot_cards(scene, people: Dict) -> List[Dict]:
    """The shots a scene will be cut into, for the card to show (DST-36).

    The same plan the renderer cuts (agents/video_agent/coverage.py), with the line
    times estimated from the script because no voice exists yet, so the cuts are
    "about" where they will fall. A medium shot's `crop` is where it looks in the
    scene's wide picture, which the interface draws from that picture (no image of
    its own); a close-up is the speaker's portrait; a cutaway is drawn at render.
    """
    cards = []
    # Which lines each shot carries, by the same estimated times the plan was cut on.
    _total, est = coverage.estimated_lines(scene.dialogue, people)
    for i, c in enumerate(coverage.plan_for_script(scene, people,
                                                   has_portrait=lambda cid: cid in people)):
        who = people.get(c.character_id) if c.character_id else None
        cards.append({
            "line_ids": [ln.line_id for ln, e in zip(scene.dialogue, est)
                         if c.start_ms <= e.start_ms < c.end_ms],
            "shot_id": coverage.shot_id_at(scene.scene_id, i),
            "framing": c.framing,
            "source": c.source,
            "move": c.move.replace("_", " "),
            "move_id": c.move,
            # The creator's own choice over the plan (DST-38): shown, and resettable.
            "overridden": coverage.shot_id_at(scene.scene_id, i) in (scene.shot_overrides or {}),
            # A close-up needs a face: only a shot that someone speaks in can have one.
            "can_close": bool(c.character_id or c.speakers),
            "character_id": c.character_id,
            "character": who.name if who else None,
            "start_ms": c.start_ms,
            "end_ms": c.end_ms,
            "crop": ({"w": c.crop[0], "x": c.crop[1], "y": c.crop[2]} if c.crop else None),
        })
    return cards


def scene_cards(script, engine: Optional[str] = None) -> List[Dict]:
    """One card per scene: title, tone, the opening camera move, and its lines."""
    from agents.audio_agent.agent import AudioAgent   # heavy import, kept local

    if script is None:
        return []
    if engine is None:
        spec = providers.active("tts")
        engine = spec.provider if spec else "edge"

    people = {c.id: c for c in script.characters.characters}
    languages = AudioAgent.speaker_languages(script)
    spoken = {}
    for c in script.characters.characters:
        # The voice the render will use: a speaker Kokoro cannot voice moves to Edge.
        used, voice_id, _code = AudioAgent.voice_plan(c, engine, languages.get(c.id))
        spoken[c.id] = {"name": c.name, "voice": _voice_label(used, voice_id)}

    cards = []
    for scene in script.scenes:
        tone = getattr(scene, "tone", "") or ""
        shots = shot_cards(scene, people)
        # Who is in the scene: whoever speaks in it, in the order they first do.
        # The script records nothing else about who is on screen.
        in_scene: List[str] = []
        for line in scene.dialogue:
            if line.character_id in people and line.character_id not in in_scene:
                in_scene.append(line.character_id)
        cards.append({
            "scene_id": scene.scene_id,
            "index": scene.index,
            "title": scene.title,
            "setting": scene.setting,
            "tone": tone,
            "characters": [{"character_id": cid, "name": people[cid].name} for cid in in_scene],
            # The establishing shot opens the scene, so its move is the one
            # worth naming on the card; the rest are in `shots`.
            "move": shots[0]["move"] if shots else "",
            "shots": shots,
            "estimated_ms": scene.duration_ms,
            "lines": [
                {
                    "line_id": line.line_id,
                    "character": spoken.get(line.character_id, {}).get(
                        "name", people.get(line.character_id).name
                        if people.get(line.character_id) else line.character_id),
                    "voice": spoken.get(line.character_id, {}).get("voice", ""),
                    "text": line.text,
                }
                for line in scene.dialogue
            ],
        })
    return cards


def _portrait_of(state, character_id: str) -> Tuple[Optional[str], bool]:
    """(path, drawn) of a character's portrait: the film's own once there is one,
    else the one the storyboard drew. `drawn` is False for a placeholder."""
    if state is None:
        return None, False
    video = state.video
    from_film = video.portrait_for(character_id) if video else None
    if from_film and Path(from_film.image_path).exists():
        return from_film.image_path, True
    stored = state.storyboard.portrait(character_id) if state.storyboard else None
    if stored and stored.path and Path(stored.path).exists():
        return stored.path, bool(stored.signature)
    return None, False


def cast(script, engine: Optional[str] = None, state=None) -> List[Dict]:
    """Who is in the film, what they look like and who will voice them.

    With `state`, each entry also carries the portrait the cast row shows. While
    a plan is still streaming there is no state yet, so the entries come without
    one and the portraits arrive as events.
    """
    from agents.audio_agent.agent import AudioAgent

    if script is None:
        return []
    if engine is None:
        spec = providers.active("tts")
        engine = spec.provider if spec else "edge"
    languages = AudioAgent.speaker_languages(script)
    out = []
    for c in script.characters.characters:
        used, voice_id, _code = AudioAgent.voice_plan(c, engine, languages.get(c.id))
        path, drawn = _portrait_of(state, c.id)
        out.append({"character_id": c.id, "name": c.name, "role": c.role,
                    "description": c.description,
                    "voice": _voice_label(used, voice_id),
                    "portrait_url": asset_url(path, versioned=True) if path else None,
                    "drawn": drawn})
    return out


def cast_note(state) -> Optional[str]:
    """What the creator is told about the cast, or None (DST-33).

    A character the creator named in their prompt who had no lines in the script
    is left out of the cast, and that is worth saying: they asked for that person.
    A character the writer invented and never used is also left out, quietly: the
    creator never expected them, and a note about it would be noise.
    """
    check = (state.metadata.get("cast_check") if state else None) or {}
    asked = [str(r["name"]) for r in check.get("removed", []) if r.get("named_in_prompt")]
    if not asked:
        return None
    if len(asked) == 1:
        return f"{asked[0]} was named in your prompt but has no lines in the script, so isn't in the cast."
    return (f"{join_names(asked)} were named in your prompt but have no lines in the script, "
            "so aren't in the cast.")


def image_budget(script) -> Dict[str, int]:
    """How many images planning and rendering each cost, from the real script.

    Planning draws one preview per scene and one portrait per character.
    Rendering draws only what planning did not: the wide shot reuses the preview,
    the portraits are kept, and a medium shot is a crop of the wide one. The one
    new picture is a detail cutaway, for a scene long enough to want it (config
    coverage.cutaway_scene_s). So more shots per scene cost no more images.
    """
    if script is None:
        return {"plan": 0, "render": 0}
    scenes = len(script.scenes)
    cutaways = sum(1 for s in script.scenes if coverage.needs_cutaway(s.duration_ms))
    return {"plan": scenes + len(script.characters.characters), "render": cutaways}
