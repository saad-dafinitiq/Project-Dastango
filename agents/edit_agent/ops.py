"""The editing operations: one set, three callers (DST-39).

A click in the storyboard, a typed sentence and the video agent's own defaults must do the
same thing, so each edit is defined once, here: a validated set of parameters and an
`apply` that changes the film's script or audio settings and says, in plain words, what it
did. What is *rendered* afterwards is not decided here: the executor runs the op as a step
and then the steps in `after` (re-cutting the film re-renders only the scenes whose plan
changed).

  click      POST /api/edit/{id}/op          -> EditAgent.apply_op -> executor step -> version
  sentence   "slow fade between scenes 1 and 2" -> classifier -> plan -> the same executor step
  video agent                                    reads the same fields (coverage.transition_into)

Every op is also registered in the internal tool registry as `edit.<name>` (mcp/tools/edit_tools).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple, Type

from pydantic import BaseModel, Field, ValidationError

from agents.audio_agent.agent import TONE_PRESETS
from agents.video_agent import camera, coverage
from shared import finish, music, transitions
from shared.schemas.audio import UploadedTrack
from shared.schemas.pipeline import PipelineState
from shared.schemas.story import EffectSpec, Scene, ShotOverride, TransitionSpec
from shared.timeline import LINE_GAP_MS, SCENE_PREROLL_MS, SCENE_TAIL_MS, estimate_line_ms


class OpError(ValueError):
    """The op cannot be applied to this film; the message says why, in a creator's words."""


class BadValue(OpError):
    """The request itself is wrong whatever the film is (an unknown mood, a length out of range): an
    API answers 400. Any other OpError depends on the film (no such scene) and answers 404."""


# ---- helpers ------------------------------------------------------------------------------

def find_scene(state: PipelineState, ref: str) -> Tuple[int, Scene]:
    """A scene by id ('scene_2'), number ('2') or 'scene 2'; (position from 0, scene)."""
    if not state.script:
        raise OpError("this film has no script yet")
    scenes = state.script.scenes
    text = str(ref).strip().lower().replace(" ", "_")
    for i, s in enumerate(scenes):
        if s.scene_id.lower() == text:
            return i, s
    digits = text.replace("scene_", "").replace("scene", "")
    if digits.isdigit() and 1 <= int(digits) <= len(scenes):
        return int(digits) - 1, scenes[int(digits) - 1]
    raise OpError(f"there is no scene {ref!r} in this film (it has {len(scenes)})")


def shot_ids(state: PipelineState, scene: Scene) -> List[str]:
    """The shot ids of a scene as they are now: from the cut film if there is one, else the plan."""
    if state.video:
        for f in state.video.frames:
            if f.scene_id == scene.scene_id and f.shots:
                return [s.shot_id for s in f.shots]
    people = {c.id: c for c in state.script.characters.characters}
    tall = state.script.story.format == "9:16"
    return [coverage.shot_id_at(scene.scene_id, i)
            for i in range(len(coverage.plan_for_script(scene, people, tall=tall)))]


def set_override(scene: Scene, shot_id: str, **fields: Any) -> None:
    """Change fields of a shot's override, keeping the others (a camera change must not
    drop a transition), and drop the override once it holds nothing."""
    current = scene.shot_overrides.get(shot_id) or ShotOverride()
    new = current.model_copy(update=fields)
    if new.move is None and new.framing is None and new.transition is None:
        scene.shot_overrides.pop(shot_id, None)
    else:
        scene.shot_overrides[shot_id] = new


def how_long(spec: TransitionSpec) -> str:
    """'slow', 'quick', or '0.6 s': a length in the words the picker uses."""
    if spec.kind == "cut" or not spec.ms:
        return ""
    for name, ms in transitions.SPEEDS.items():
        if ms == spec.ms and name != "normal":
            return name
    return "" if spec.ms == transitions.NORMAL_MS else f"{spec.ms / 1000:g} s"


# ---- set_transition -----------------------------------------------------------------------

class SetTransition(BaseModel):
    scene: str = Field(..., description="the scene the cut comes into: 'scene_2' or '2'")
    shot: Optional[int] = Field(default=None, ge=1,
                                description="which shot of that scene comes in (1 or none: from the previous scene)")
    transition: Optional[str] = Field(default=None, description="a style id (see /api/edit/ops)")
    speed: Optional[str] = Field(default=None, description="quick, normal or slow")
    ms: Optional[int] = Field(default=None, description="or a length in milliseconds")
    reset: bool = Field(default=False, description="back to the default for this cut")


def _apply_set_transition(state: PipelineState, p: SetTransition) -> str:
    i, scene = find_scene(state, p.scene)
    n = p.shot or 1
    if n == 1 and i == 0:
        raise OpError("nothing comes before the first shot of the film, so it has no transition")
    ids = shot_ids(state, scene)
    if n > len(ids):
        raise OpError(f"scene {i + 1} has {len(ids)} shots, so there is no shot {n}")
    if p.reset:
        spec = None
        words = "back to the default"
    else:
        if not p.transition:
            raise BadValue("which transition? " + ", ".join(s.id for s in transitions.styles()))
        style = transitions.get(p.transition)
        ms = 0 if style.id == "cut" else transitions.speed_ms(p.speed, p.ms)
        spec = TransitionSpec(kind=style.id, ms=ms or None)
        words = " ".join(w for w in (how_long(spec), style.label.lower()) if w)
    if n == 1:
        scene.cut_in = spec
        where = f"Between scenes {i} and {i + 1}"
    else:
        set_override(scene, ids[n - 1], transition=spec)
        where = f"Scene {i + 1}, shot {n}"
    return f"{where}: {words}"


# ---- set_camera (DST-38) ------------------------------------------------------------------

class SetCamera(BaseModel):
    scene: str
    shot_id: str
    move: Optional[str] = None
    framing: Optional[str] = None
    reset: bool = False


def _apply_set_camera(state: PipelineState, p: SetCamera) -> str:
    i, scene = find_scene(state, p.scene)
    if p.shot_id not in shot_ids(state, scene):
        raise OpError(f"unknown shot {p.shot_id!r} in {scene.scene_id}")
    if p.move is not None and p.move not in camera.MOVES:
        raise BadValue(f"unknown camera move {p.move!r}")
    if p.framing is not None and p.framing not in ("wide", "medium", "close"):
        raise BadValue(f"unknown framing {p.framing!r}")
    if p.reset:
        set_override(scene, p.shot_id, move=None, framing=None)
        what = "camera as planned"
    else:
        fields = {k: v for k, v in (("move", p.move), ("framing", p.framing)) if v is not None}
        if not fields:
            raise BadValue("choose a camera move, a framing, or reset")
        set_override(scene, p.shot_id, **fields)
        what = ", ".join(x for x in (p.move.replace("_", " ") if p.move else "",
                                     f"{p.framing} framing" if p.framing else "") if x)
    return f"Scene {i + 1}: {what}"


# ---- audio: music, volume, ambience (DST-39) -----------------------------------------------

AUDIO_AFTER = ("remix_audio", "recompose_video")
NEEDS_FILM = "render the film first: its sound is only made then"


def _scenes_in_scope(state: PipelineState, ref: Optional[str]) -> List[Tuple[int, Scene]]:
    if not state.script:
        raise OpError("this film has no script yet")
    if ref:
        i, scene = find_scene(state, ref)
        return [(i, scene)]
    return list(enumerate(state.script.scenes))


def _need_audio(state: PipelineState):
    if not state.audio:
        raise OpError(NEEDS_FILM)
    return state.audio


class SetMusic(BaseModel):
    scene: Optional[str] = Field(default=None, description="one scene, or none for the whole film")
    mood: Optional[str] = Field(default=None, description="a mood: " + ", ".join(
        ("ambient", "tense", "joyful", "mysterious", "epic", "sad", "ominous", "ethereal", "energetic", "neutral")))
    track: Optional[str] = Field(default=None, description="a library track id or upload:<id> (see /api/music)")
    off: bool = Field(default=False, description="no music (in this scene, or the whole film)")
    reset: bool = Field(default=False, description="music as it was made")


def _apply_set_music(state: PipelineState, p: SetMusic) -> str:
    scenes = _scenes_in_scope(state, p.scene)
    whole = p.scene is None
    where = "The music" if whole else f"Scene {scenes[0][0] + 1}'s music"
    if p.reset:
        audio = _need_audio(state)
        audio.bgm_enabled = True
        if whole:
            audio.music_track, audio.scene_music = None, {}
        else:
            audio.scene_music.pop(scenes[0][1].scene_id, None)
        return f"{where}: back as it was made"
    if p.off:
        audio = _need_audio(state)
        if whole:
            audio.bgm_enabled = False
        else:
            audio.scene_music[scenes[0][1].scene_id] = "none"
        return f"{where}: off"
    if p.track:
        audio = _need_audio(state)
        try:
            if p.track.startswith("upload:") and not any(f"upload:{u.id}" == p.track for u in audio.uploads):
                # Stored by the upload route; the film learns of it when it is first used.
                audio.uploads.append(UploadedTrack(**music.load_upload(state.project_id, p.track.split(":", 1)[1])))
            music.track_path(p.track, audio.uploads)
        except music.MusicError as e:
            raise BadValue(str(e)) from None
        title = (library_title(p.track) or next((u.title for u in audio.uploads if f"upload:{u.id}" == p.track), p.track))
        audio.bgm_enabled = True
        if whole:
            audio.music_track, audio.scene_music = p.track, {}
        else:
            audio.scene_music[scenes[0][1].scene_id] = p.track
        return f"{where}: {title}"
    if p.mood:
        key = p.mood.lower().strip()
        if key not in music.moods() and key not in music.ALIASES:
            raise BadValue(f"which mood? {', '.join(music.moods())}")
        mood = music.resolve_mood(key)
        for i, scene in scenes:
            scene.music_mood = mood
            if state.audio:
                state.audio.scene_music.pop(scene.scene_id, None)
                if not whole and state.audio.music_track:
                    # A film-wide track outranks a mood, so this scene is given the mood's track.
                    t = music.for_mood(mood, i)
                    if t:
                        state.audio.scene_music[scene.scene_id] = t.id
        if state.audio:
            state.audio.bgm_enabled = True
            if whole:
                state.audio.music_track = None
        return f"{where}: {mood}"
    raise BadValue("what about the music? a mood, a track, off, or reset")


def library_title(track_id: str) -> Optional[str]:
    t = music.library().get(track_id)
    return t.title if t else None


class SetVolume(BaseModel):
    target: Literal["music", "voices", "ambience"]
    percent: int = Field(..., ge=0, le=300, description="100 is as the film was made")


_VOLUME_FIELDS = {"music": ("music_volume", 0.32, 1.5), "voices": ("voice_volume", 1.0, 2.0),
                  "ambience": ("ambience_volume", 0.22, 1.0)}


def _apply_set_volume(state: PipelineState, p: SetVolume) -> str:
    audio = _need_audio(state)
    field_name, base, top = _VOLUME_FIELDS[p.target]
    value = base * p.percent / 100.0
    if value > top:
        raise BadValue(f"{p.target} cannot go past {int(top / base * 100)}%")
    setattr(audio, field_name, round(value, 4))
    return f"{p.target.capitalize()} at {p.percent}%"


class SetAmbience(BaseModel):
    scene: Optional[str] = Field(default=None, description="one scene, or none for the whole film")
    kind: Optional[str] = Field(default=None, description="rain, wind, city, sea, night, or none")
    reset: bool = False


def _apply_set_ambience(state: PipelineState, p: SetAmbience) -> str:
    audio = _need_audio(state)
    scenes = _scenes_in_scope(state, p.scene)
    key = "*" if p.scene is None else scenes[0][1].scene_id
    where = "The film" if p.scene is None else f"Scene {scenes[0][0] + 1}"
    if p.reset:
        if p.scene is None:
            audio.ambience = {}
        else:
            audio.ambience.pop(key, None)
        return f"{where}: no added ambience"
    kind = (p.kind or "").lower().strip()
    if kind != "none" and kind not in music.AMBIENCE:
        raise BadValue("which ambience? " + ", ".join([*music.AMBIENCE, "none"]))
    if p.scene is None:
        # A film-wide choice replaces what scenes had, so it is what is heard everywhere.
        audio.ambience = {"*": kind}
    else:
        audio.ambience[key] = kind
    return f"{where}: {'no ambience' if kind == 'none' else music.AMBIENCE[kind]['about']}"


# ---- set_graphics: titles over the film (DST-39) --------------------------------------------

GRAPHICS = ("title_card", "scene_titles", "lower_thirds", "end_card")
GRAPHIC_WORDS = {"title_card": "title card", "scene_titles": "scene titles",
                 "lower_thirds": "name bars", "end_card": "end card"}


class SetGraphics(BaseModel):
    title_card: Optional[bool] = Field(default=None, description="a title card over the opening")
    title_text: Optional[str] = Field(default=None, max_length=90, description="its title (default: the film's)")
    scene_titles: Optional[bool] = Field(default=None, description="each scene's title, top left, as it begins")
    lower_thirds: Optional[bool] = Field(default=None, description="a name bar the first time a character speaks")
    end_card: Optional[bool] = Field(default=None, description="an end card over the closing seconds")
    end_text: Optional[str] = Field(default=None, max_length=90, description="replaces 'The End'")
    credits_text: Optional[str] = Field(default=None, max_length=90, description="a line under the end card")
    reset: bool = Field(default=False, description="no titles at all")


def _apply_set_graphics(state: PipelineState, p: SetGraphics) -> str:
    if not state.script:
        raise OpError("this film has no script yet")
    spec = state.script.story.graphics
    if p.reset:
        state.script.story.graphics = type(spec)()
        return "Titles: none"
    said = []
    for name in GRAPHICS:
        value = getattr(p, name)
        if value is not None:
            setattr(spec, name, value)
            said.append(f"{GRAPHIC_WORDS[name]} {'on' if value else 'off'}")
    for field_name, words in (("title_text", "title"), ("end_text", "end text"), ("credits_text", "credits line")):
        value = getattr(p, field_name)
        if value is not None:
            setattr(spec, field_name, value.strip() or None)
            said.append(f"{words} {'set' if value.strip() else 'back to the default'}")
    if not said:
        raise BadValue("which titles? " + ", ".join(GRAPHIC_WORDS.values()))
    # Text with its graphic still off would never be seen; say so rather than save a no-op.
    if p.title_text and not spec.title_card:
        raise BadValue("turn the title card on to give it a title")
    if (p.end_text or p.credits_text) and not spec.end_card:
        raise BadValue("turn the end card on to give it text")
    return "Titles: " + ", ".join(said)


# ---- changes to scenes, shots and voices after a render (DST-39) ------------------------------

def _need_film(state: PipelineState) -> None:
    if not (state.video and state.video.final_video_path):
        raise OpError("render the film first: there is nothing to redraw yet")


class RegenerateShot(BaseModel):
    scene: str
    shot: int = Field(..., ge=1, description="which shot of the scene (1 is the first)")


def _apply_regenerate_shot(state: PipelineState, p: RegenerateShot) -> str:
    _need_film(state)
    i, scene = find_scene(state, p.scene)
    ids = shot_ids(state, scene)
    if p.shot > len(ids):
        raise OpError(f"scene {i + 1} has {len(ids)} shots, so there is no shot {p.shot}")
    return f"Scene {i + 1}, shot {p.shot}: drawn again"


def find_line(scene: Scene, ref: Any):
    """A line of a scene by number (1 is the first) or by id."""
    text = str(ref).strip()
    if text.isdigit() and 1 <= int(text) <= len(scene.dialogue):
        return int(text), scene.dialogue[int(text) - 1]
    for n, ln in enumerate(scene.dialogue, 1):
        if ln.line_id == text:
            return n, ln
    raise OpError(f"{scene.scene_id} has {len(scene.dialogue)} lines, so there is no line {ref!r}")


class EditLine(BaseModel):
    scene: str
    line: str = Field(..., description="the line's number in the scene (1 is the first)")
    text: str = Field(..., min_length=1, max_length=300)


def _apply_edit_line(state: PipelineState, p: EditLine) -> str:
    i, scene = find_scene(state, p.scene)
    n, line = find_line(scene, p.line)
    text = p.text.strip()
    if not text:
        raise BadValue("what should the line say?")
    line.text = text
    line.duration_ms = estimate_line_ms(text)
    # The scene's estimated length follows its lines, as on the storyboard.
    scene.duration_ms = (SCENE_PREROLL_MS + SCENE_TAIL_MS + sum(ln.duration_ms for ln in scene.dialogue)
                         + LINE_GAP_MS * max(0, len(scene.dialogue) - 1))
    if state.storyboard:
        for f in state.storyboard.frames:
            if f.scene_id == scene.scene_id:
                for sl in f.dialogue:
                    if sl.line_id == line.line_id:
                        sl.text = text
    return f'Scene {i + 1}, line {n}: "{text[:48]}{"…" if len(text) > 48 else ""}"'


def find_character(state: PipelineState, ref: str):
    people = state.script.characters.characters if state.script else []
    key = str(ref).strip().lower()
    for c in people:
        if key in (c.id.lower(), c.name.lower(), c.id.lower().replace("char_", "")):
            return c
    raise OpError(f"there is no character {ref!r}; the cast is {', '.join(c.name for c in people)}")


class SetVoice(BaseModel):
    character: str = Field(..., description="the character's name or id")
    tone: Optional[str] = Field(default=None, description="how they sound: " + ", ".join(TONE_PRESETS))
    different: bool = Field(default=False, description="a different voice for them")


def _apply_set_voice(state: PipelineState, p: SetVoice) -> str:
    if not state.audio:
        raise OpError(NEEDS_FILM)
    c = find_character(state, p.character)
    if p.tone is not None and p.tone.lower() not in TONE_PRESETS:
        raise BadValue("which tone? " + ", ".join(TONE_PRESETS))
    if p.tone is None and not p.different:
        raise BadValue(f"what about {c.name}'s voice? a tone, or a different voice")
    if not any(s.character_id == c.id for s in state.audio.manifest.segments if s.kind == "dialogue"):
        raise OpError(f"{c.name} has no lines to record")
    what = " ".join(w for w in ((p.tone or "").lower(), "a different voice" if p.different else "voice") if w)
    return f"{c.name}: {what}" if p.different or not p.tone else f"{c.name}: {p.tone.lower()} voice"


class RemoveScene(BaseModel):
    scene: str


def _renumber(state: PipelineState) -> None:
    for pos, s in enumerate(state.script.scenes):
        s.index = pos
    if state.storyboard:
        order = {s.scene_id: pos for pos, s in enumerate(state.script.scenes)}
        state.storyboard.frames = sorted((f for f in state.storyboard.frames if f.scene_id in order),
                                         key=lambda f: order[f.scene_id])
        for f in state.storyboard.frames:
            f.index = order[f.scene_id]


def _apply_remove_scene(state: PipelineState, p: RemoveScene) -> str:
    i, scene = find_scene(state, p.scene)
    if len(state.script.scenes) < 2:
        raise OpError("a film needs at least one scene")
    state.script.scenes.pop(i)
    _renumber(state)
    if state.audio:
        state.audio.scene_voices.pop(scene.scene_id, None)
        state.audio.scene_music.pop(scene.scene_id, None)
        state.audio.ambience.pop(scene.scene_id, None)
    return f"Scene {i + 1} ({scene.title}) removed"


class MoveScene(BaseModel):
    scene: str
    to: int = Field(..., ge=1, description="its new place in the film (1 is the first)")


def _apply_move_scene(state: PipelineState, p: MoveScene) -> str:
    i, scene = find_scene(state, p.scene)
    n = len(state.script.scenes)
    if p.to > n:
        raise OpError(f"the film has {n} scenes, so there is no place {p.to}")
    if p.to - 1 == i:
        raise OpError(f"scene {i + 1} is already in place {p.to}")
    state.script.scenes.insert(p.to - 1, state.script.scenes.pop(i))
    _renumber(state)
    return f"Scene {i + 1} ({scene.title}) moved to place {p.to}"


class SetEffect(BaseModel):
    scene: Optional[str] = Field(default=None, description="one scene, or none for the whole film")
    effect: Optional[str] = Field(default=None, description=", ".join([*finish.EFFECTS, "none"]))
    strength: str = Field(default="normal", description="soft, normal or strong")
    reset: bool = False


def _apply_set_effect(state: PipelineState, p: SetEffect) -> str:
    scenes = _scenes_in_scope(state, p.scene)
    where = "The film" if p.scene is None else f"Scene {scenes[0][0] + 1}"
    kind = (p.effect or "").lower().strip()
    if p.reset or kind == "none":
        for _i, s in scenes:
            s.effect = None
        return f"{where}: no effect"
    if kind not in finish.EFFECTS:
        raise BadValue("which effect? " + ", ".join([*finish.EFFECTS, "none"]))
    if p.strength not in finish.STRENGTHS:
        raise BadValue("how strong? " + ", ".join(finish.STRENGTHS))
    for _i, s in scenes:
        s.effect = EffectSpec(kind=kind, strength=p.strength)
    how = "" if p.strength == "normal" else p.strength + " "
    return f"{where}: {how}{finish.EFFECTS[kind]['label'].lower()}"


def after_steps(state: Optional[PipelineState], name: str, params: Mapping[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """The executor steps that follow an op on a finished film, as (step, scope, params).

    One place builds them, for a click and for a typed sentence both, so they cannot differ."""
    params = dict(params or {})
    steps = []
    for step in get(name).after:
        scope, step_params = "global", dict(params)
        if step == "rerun_audio" and name == "set_voice":
            who = find_character(state, params['character']).id if state else params['character']
            scope = f"character:{who}"
            step_params = {k: v for k, v in (("tone", params.get("tone")),
                                             ("voice", "alternate" if params.get("different") else None))
                           if v is not None}
        steps.append((step, scope, step_params))
    return steps


# ---- the table ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Op:
    name: str
    summary: str
    params: Type[BaseModel]
    apply: Callable[[PipelineState, Any], str]
    # Executor steps that follow on a finished film: cutting it again re-renders only
    # the scenes whose plan changed.
    after: Tuple[str, ...] = ("recompose_video",)
    options: Callable[[], Dict[str, Any]] = field(default=lambda: {})


def _transition_options() -> Dict[str, Any]:
    return {"styles": [{"id": s.id, "label": s.label, "about": s.about, "main": s.main}
                       for s in transitions.styles()],
            "speeds": dict(transitions.SPEEDS), "min_ms": transitions.MIN_MS,
            "max_ms": transitions.MAX_MS}


def _music_options() -> Dict[str, Any]:
    return {"moods": music.moods(), "tracks": music.catalogue(),
            "ambience": [{"id": k, "label": v["label"], "about": v["about"]} for k, v in music.AMBIENCE.items()],
            "max_upload_mb": music.MAX_UPLOAD_BYTES // (1024 * 1024)}


OPS: Dict[str, Op] = {
    "set_transition": Op("set_transition", "How one cut comes in: a cut, a fade, a wipe and more.",
                         SetTransition, _apply_set_transition, options=_transition_options),
    "set_music": Op("set_music", "The music: a mood, a track, off, or as made.", SetMusic,
                    _apply_set_music, after=AUDIO_AFTER, options=_music_options),
    "set_volume": Op("set_volume", "How loud the music, the voices or the ambience is.", SetVolume,
                     _apply_set_volume, after=AUDIO_AFTER),
    "set_ambience": Op("set_ambience", "Rain, wind, a city or the sea under a scene or the film.",
                       SetAmbience, _apply_set_ambience, after=AUDIO_AFTER, options=_music_options),
    "set_graphics": Op("set_graphics", "Titles over the film: a title card, scene titles, name bars, an end card.",
                       SetGraphics, _apply_set_graphics),
    "regenerate_shot": Op("regenerate_shot", "Draw one shot's picture again.", RegenerateShot,
                          _apply_regenerate_shot, after=("redraw_shot", "recompose_video")),
    "edit_line": Op("edit_line", "Change one line and record just that line again.", EditLine,
                    _apply_edit_line, after=("rerecord_line", "recompose_video")),
    "set_voice": Op("set_voice", "How one character sounds, or a different voice for them.", SetVoice,
                    _apply_set_voice, after=("rerun_audio", "recompose_video")),
    "remove_scene": Op("remove_scene", "Take a scene out of the film.", RemoveScene,
                       _apply_remove_scene, after=("restructure", "recompose_video")),
    "move_scene": Op("move_scene", "Move a scene to another place in the film.", MoveScene,
                     _apply_move_scene, after=("restructure", "recompose_video")),
    "set_effect": Op("set_effect", "A look over a scene or the film: grain, vignette, glow, cinema bars, glitch.",
                     SetEffect, _apply_set_effect,
                     options=lambda: {"effects": [{"id": k, **v} for k, v in finish.EFFECTS.items()],
                                      "strengths": list(finish.STRENGTHS)}),
    "set_camera": Op("set_camera", "One shot's camera move or framing.", SetCamera,
                     _apply_set_camera),
}


def get(name: str) -> Op:
    op = OPS.get(name)
    if op is None:
        raise OpError(f"unknown edit {name!r}: use one of {', '.join(OPS)}")
    return op


def parse(name: str, params: Mapping[str, Any]) -> BaseModel:
    """The op's parameters, validated; a bad one raises OpError naming the field."""
    op = get(name)
    try:
        return op.params.model_validate(dict(params or {}))
    except ValidationError as e:
        first = e.errors()[0]
        field_name = ".".join(str(x) for x in first["loc"]) or "parameters"
        raise OpError(f"{field_name}: {first['msg']}") from None


def _run(op: Op, state: PipelineState, parsed: BaseModel) -> str:
    """Apply an op; whatever it refuses with (a transition the server's ffmpeg lacks, a length
    out of range) reaches the caller as an OpError, so every caller handles one kind of refusal."""
    try:
        return op.apply(state, parsed)
    except OpError:
        raise
    except ValueError as e:
        raise BadValue(str(e)) from None


def check(state: PipelineState, name: str, params: Mapping[str, Any]) -> str:
    """Would this op apply to this film? Tries it on a copy; returns what it would do, in words."""
    return _run(get(name), state.model_copy(deep=True), parse(name, params))


def apply(state: PipelineState, name: str, params: Mapping[str, Any]) -> str:
    """Apply the op to `state` in place; returns what it did, in words."""
    return _run(get(name), state, parse(name, params))


def check_many(state: PipelineState, items: List[Mapping[str, Any]]) -> List[str]:
    """Would these ops apply, one after the other, to this film? Tries them in order on a copy (a
    later one sees what an earlier one did) and returns what each would do, in words."""
    copy = state.model_copy(deep=True)
    labels: List[str] = []
    for it in items:
        labels.append(_run(get(it["op"]), copy, parse(it["op"], it.get("params") or {})))
    return labels


_JOINS = ("remix_audio", "recompose_video")


def after_steps_many(state: PipelineState, items: List[Mapping[str, Any]]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """The steps that follow a whole list of ops on a finished film, as (step, scope, params).

    The sound is mixed once and the film is cut once, at the end, however many ops asked for it:
    that is what makes applying ten changes together faster than ten changes one by one."""
    seen, middle, joins = set(), [], []
    for it in items:
        for step, scope, params in after_steps(state, it["op"], it.get("params") or {}):
            if step in _JOINS:
                if step not in joins:
                    joins.append(step)
                continue
            key = (step, scope, repr(sorted(params.items())))
            if key not in seen:
                seen.add(key)
                middle.append((step, scope, params))
    return middle + [(j, "global", {}) for j in _JOINS if j in joins]


def catalogue() -> Dict[str, Any]:
    """What the interface and the edit agent may offer: every op, its parameters, its options."""
    return {"ops": [{"name": o.name, "summary": o.summary, "schema": o.params.model_json_schema(),
                     "options": o.options()} for o in OPS.values()]}
