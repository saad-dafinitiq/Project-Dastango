"""HTTP endpoints to launch and re-run the main pipeline.

Starting a run enqueues a job and returns immediately; a worker picks it up.
The response carries the job id so the caller can follow, cancel or retry it.
"""
from __future__ import annotations
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import jobs
from auth import accounts
from auth.deps import require_project, require_user
from auth.accounts import User
from agents.story_agent import current_length_check
from agents.orchestrator import RENDER_DESCRIPTIONS as RENDERS
from shared import film_format, stand_in, voices
from agents.orchestrator.storyboard_view import cap_transitions, cast, cast_note, image_budget, scene_cards
from shared.assets import asset_url
from shared.languages import iso639_1, supported_names
from shared.utils.files import project_dir
from shared.utils.ids import new_project_id
from shared.utils.logging import get_logger
from state_manager.state_manager import StateManager

from ..services import pipeline_service, progress

router = APIRouter()
log = get_logger("api.pipeline")
sm = StateManager()


class RunRequest(BaseModel):
    prompt: str = Field(..., min_length=4)
    target_duration_s: int = Field(45, ge=15, le=600)
    scene_count: int = Field(4, ge=1, le=12)
    format: str = "16:9"          # DST-34: 16:9, 9:16 or 1:1
    style: str = "realistic"      # realistic or anime
    with_bgm: bool = True
    with_subtitles: bool = True
    subtitle_language: str = "English"
    tts_engine: Optional[str] = None   # None = the provider chain's choice


class RunResponse(BaseModel):
    project_id: str
    job_id: str
    status: str
    websocket: str


class PhaseRerunRequest(BaseModel):
    project_id: str
    phase: str  # "story" | "audio" | "video"


def _check_engine(name: Optional[str]) -> None:
    """Reject an unusable voice engine now, not three minutes into a render."""
    if not name:
        return
    engine = voices.get(name)
    if engine is None:
        raise HTTPException(400, f"unknown voice engine {name!r}")
    reason = voices.unavailable_reason(engine)
    if reason:
        raise HTTPException(409, f"{engine.label} is not available here: {reason}")


def _accepted(kind: str, project_id: str, status: str, owner: User,
              max_attempts: int = 2, **payload) -> RunResponse:
    payload = {k: v for k, v in payload.items() if v is not None}
    # Record the owner before the job exists, so a worker can't finish a render
    # into a project nobody is responsible for.
    accounts.register_project(project_id, owner.id)
    job = jobs.enqueue(kind, project_id, payload, max_attempts=max_attempts)
    log.info("queued %s %s for %s", kind, job.id, project_id)
    return RunResponse(project_id=project_id, job_id=job.id, status=status,
                       websocket=f"/ws/progress/{project_id}")


@router.post("/run", response_model=RunResponse)
def start_run(req: RunRequest, user: User = Depends(require_user)):
    """Queue a full pipeline run; progress streams over /ws/progress/{project_id}."""
    _check_engine(req.tts_engine)
    fmt, style = _check_spec(req.format, req.style)
    return _accepted(
        "run_full", new_project_id(), "queued", user,
        prompt=req.prompt, target_duration_s=req.target_duration_s,
        scene_count=req.scene_count, format=fmt, style=style, with_bgm=req.with_bgm,
        with_subtitles=req.with_subtitles, subtitle_language=req.subtitle_language,
        tts_engine=req.tts_engine,
    )


@router.post("/rerun", response_model=RunResponse)
def rerun_phase(req: PhaseRerunRequest, user: User = Depends(require_user)):
    if req.phase not in ("story", "audio", "video"):
        raise HTTPException(400, f"unknown phase {req.phase}")
    # The project id arrives in the body, so the check happens here rather
    # than through the path dependency.
    if not accounts.may_access(user, req.project_id) or not sm.latest(req.project_id):
        raise HTTPException(404, f"project {req.project_id} not found")
    return _accepted("rerun_phase", req.project_id, "queued", user, phase=req.phase)


def _check_spec(format: str, style: str):
    """The creator's format and style, or a 400 that names what is accepted."""
    try:
        return film_format.check_format(format), film_format.check_style(style)
    except ValueError as e:
        raise HTTPException(400, str(e))


class SpecChange(BaseModel):
    format: Optional[str] = None
    style: Optional[str] = None


class PlanRequest(BaseModel):
    prompt: str = Field(..., min_length=4)
    target_duration_s: int = Field(45, ge=15, le=600)
    scene_count: int = Field(4, ge=1, le=12)
    format: str = "16:9"          # DST-34: 16:9, 9:16 or 1:1
    style: str = "realistic"      # realistic or anime
    with_preview: bool = True


class SceneEdit(BaseModel):
    title: Optional[str] = None
    setting: Optional[str] = None
    visual_prompt: Optional[str] = None
    dialogue: Optional[Dict[str, str]] = None   # line_id -> new text


class ShotChange(BaseModel):
    move: Optional[str] = None
    framing: Optional[str] = None
    reset: bool = False


class RenderRequest(BaseModel):
    with_bgm: bool = True
    with_subtitles: bool = True
    subtitle_language: str = "English"
    burn_subtitles: bool = True
    tts_engine: Optional[str] = None


@router.post("/plan", response_model=RunResponse)
def start_plan(req: PlanRequest, user: User = Depends(require_user)):
    """Write the script + preview images only; render is a separate, approved step."""
    fmt, style = _check_spec(req.format, req.style)
    return _accepted(
        "plan", new_project_id(), "planning", user,
        prompt=req.prompt, target_duration_s=req.target_duration_s,
        scene_count=req.scene_count, format=fmt, style=style, with_preview=req.with_preview,
    )


@router.get("/storyboard/{project_id}", dependencies=[Depends(require_project)])
def get_storyboard(project_id: str, engine: Optional[str] = None):
    """The storyboard, as cards.

    `engine` names the voice engine the creator is about to render with, so
    each line shows the voice that will actually speak it.
    """
    if engine and voices.get(engine) is None:
        raise HTTPException(400, f"unknown voice engine {engine!r}")
    state = sm.latest(project_id)
    if not state or not state.storyboard:
        raise HTTPException(404, f"no storyboard for {project_id}")
    board = state.storyboard.model_dump(mode="json")
    # The same cards the live plan streamed, so a reload looks identical.
    legacy = bool(state.video and state.video.legacy_transitions)
    card_list = scene_cards(state.script, engine, legacy=legacy)
    if state.video:
        # A cut film has real shot lengths: a transition's limit is half the shorter shot (DST-43).
        cap_transitions(card_list, {s.shot_id: s.duration_ms for f in state.video.frames for s in f.shots})
    cards = {c["scene_id"]: c for c in card_list}
    for frame in board["frames"]:
        if frame.get("preview_path"):
            frame["preview_url"] = asset_url(frame["preview_path"], versioned=True)
        card = cards.get(frame["scene_id"], {})
        frame["tone"] = card.get("tone", "")
        frame["move"] = card.get("move", "")
        frame["shots"] = card.get("shots", [])
        frame["lines"] = card.get("lines", [])
        frame["characters"] = card.get("characters", [])
    board["cast"] = cast(state.script, engine, state)
    # Said only when a character the creator named had no lines (DST-33).
    board["cast_note"] = cast_note(state)
    board["images"] = image_budget(state.script)
    # What the script's length check found (DST-28), so a reload says what the plan did.
    board["length"] = current_length_check(state)
    board["stage"] = state.stage
    board["version"] = state.version
    story = state.script.story if state.script else None
    board["spec"] = {"format": story.format if story else film_format.DEFAULT_FORMAT,
                     "style": (story.style if story else "") or "",
                     # What the scene-count check found: the creator asked for `asked` scenes.
                     "scene_check": state.metadata.get("scene_check")}
    return board


# What the creator is told when the words were saved but the picture could not be
# redrawn. It names no provider: the reason is in the server log.
REDRAW_FAILED = ("Your changes are saved, but the picture could not be redrawn just now; "
                 "the image services may be at their limit. The previous picture is kept, "
                 "and the film will draw this scene fresh when you render.")


@router.patch("/storyboard/{project_id}/{scene_id}",
              dependencies=[Depends(require_project)])
def edit_storyboard(project_id: str, scene_id: str, edit: SceneEdit):
    """Edit one scene before rendering; redraws its preview if the visuals changed.

    The words are always saved, as a new version. The answer is the storyboard,
    plus a `redraw` field when a new picture was asked for: `ok` false means it
    could not be drawn and the previous picture was kept.
    """
    # A plan, render or edit writes versions through the queue, one job per film at
    # a time. This edit writes one directly, so two could overwrite each other.
    busy = [j for j in jobs.list_jobs(project_id=project_id) if not j.done]
    if busy:
        raise HTTPException(409, f"This film is being worked on right now ({busy[0].kind}); "
                                 "try the edit again when it has finished.")
    outcome: dict = {}
    try:
        pipeline_service.orchestrator().update_storyboard(
            project_id, scene_id, title=edit.title, setting=edit.setting,
            visual_prompt=edit.visual_prompt, dialogue=edit.dialogue, outcome=outcome,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    board = get_storyboard(project_id)
    redraw = outcome.get("redraw")
    if redraw:
        board["redraw"] = {**redraw, **({} if redraw["ok"] else {"message": REDRAW_FAILED})}
    return board


@router.get("/shot-options")
def shot_options():
    """The camera moves and framings a creator can choose for a shot (DST-38)."""
    from agents.video_agent import camera
    return {
        "moves": [{"id": k, "label": k.replace("_", " "), "about": m.description}
                  for k, m in camera.MOVES.items()],
        "framings": [{"id": "wide", "about": "the whole picture"},
                     {"id": "medium", "about": "closer on part of the picture, no new image"},
                     {"id": "close", "about": "the speaker's face, from their portrait"}],
    }


@router.patch("/storyboard/{project_id}/{scene_id}/shots/{shot_id}",
              dependencies=[Depends(require_project)])
def edit_shot(project_id: str, scene_id: str, shot_id: str, change: ShotChange,
              user: User = Depends(require_user)):
    """Choose one shot's camera move and/or framing (DST-38, F4).

    Before a render this is a storyboard edit and answers at once with the
    storyboard. On a finished film the film has to be cut again, which is a job
    like any change: the answer is `{job_id, ...}` and progress streams as usual.
    """
    if not (change.reset or change.move or change.framing):
        raise HTTPException(400, "choose a camera move, a framing, or reset")
    busy = [j for j in jobs.list_jobs(project_id=project_id) if not j.done]
    if busy:
        raise HTTPException(409, f"This film is being worked on right now ({busy[0].kind}); "
                                 "try again when it has finished.")
    state = sm.latest(project_id)
    if not state or not state.script:
        raise HTTPException(404, f"no storyboard for {project_id}")
    from agents.video_agent import camera
    if change.move is not None and change.move not in camera.MOVES:
        raise HTTPException(400, f"unknown camera move {change.move!r}")
    if change.framing is not None and change.framing not in ("wide", "medium", "close"):
        raise HTTPException(400, f"unknown framing {change.framing!r}")
    if state.video and state.video.final_video_path:
        # Refuse a scene or shot that is not there now, rather than queue a job that fails.
        frame = next((f for f in state.video.frames if f.scene_id == scene_id), None)
        if frame is None or (frame.shots and shot_id not in {s.shot_id for s in frame.shots}):
            raise HTTPException(404, f"unknown shot '{shot_id}' in {scene_id}")
        run = _accepted("shot", project_id, "queued", user, max_attempts=1,
                        scene_id=scene_id, shot_id=shot_id, move=change.move,
                        framing=change.framing, reset=change.reset or None)
        return run.model_dump()
    try:
        pipeline_service.orchestrator().set_shot(
            project_id, scene_id, shot_id, move=change.move, framing=change.framing,
            reset=change.reset)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return get_storyboard(project_id)


@router.patch("/spec/{project_id}", response_model=RunResponse,
              dependencies=[Depends(require_project)])
def change_spec(project_id: str, change: SpecChange, user: User = Depends(require_user)):
    """Change a storyboard's format and/or style (DST-34). Every picture is drawn again, so
    this uses image quota; it is only offered before the render (a finished film is not
    converted: the ticket leaves that out, and it would need every shot again as well)."""
    if change.format is None and change.style is None:
        raise HTTPException(400, "choose a format, a style, or both")
    state = sm.latest(project_id)
    if not state or not state.script:
        raise HTTPException(404, f"no storyboard for {project_id}")
    if state.video and state.video.final_video_path:
        raise HTTPException(409, "This film is already rendered, so its format and style are fixed. "
                                 "Make a new film to try another look.")
    fmt, style = _check_spec(change.format if change.format is not None else state.script.story.format,
                             change.style if change.style is not None else (state.script.story.style or "realistic"))
    busy = [j for j in jobs.list_jobs(project_id=project_id) if not j.done]
    if busy:
        raise HTTPException(409, f"This film is being worked on right now ({busy[0].kind}); "
                                 "try again when it has finished.")
    return _accepted("respec", project_id, "queued", user, max_attempts=1, format=fmt, style=style)


# Jobs that change or render a film: a render waits for them to finish rather than queue behind them.
RENDER_BLOCKERS = ("op", "ops", "edit", "shot", "revert", "render", "respec")


@router.post("/render/{project_id}", response_model=RunResponse)
def start_render(project_id: str, req: RenderRequest,
                 user: User = Depends(require_project)):
    """Approve a storyboard and render the film."""
    state = sm.latest(project_id)
    if not state or not state.script:
        raise HTTPException(404, f"no storyboard to render for {project_id}")
    _check_engine(req.tts_engine)
    # Not while the film is being changed or already rendered: a render queued behind an edit starts
    # again from the script and spends minutes redrawing a film that was being edited (DST-41), and a
    # double click would queue two. (A render may still follow its own plan: that is not listed.)
    busy = [j for j in jobs.list_jobs(project_id=project_id) if not j.done and j.kind in RENDER_BLOCKERS]
    if busy:
        raise HTTPException(409, f"This film is being worked on right now ({busy[0].kind}); "
                                 "try again when it has finished.")
    return _accepted(
        "render", project_id, "rendering", user,
        with_bgm=req.with_bgm, with_subtitles=req.with_subtitles,
        subtitle_language=req.subtitle_language, burn_subtitles=req.burn_subtitles,
        tts_engine=req.tts_engine,
    )


@router.get("/subtitles/{project_id}", dependencies=[Depends(require_project)])
def subtitle_tracks(project_id: str):
    """WebVTT tracks for the browser player (MP4 soft subs are invisible there)."""
    state = sm.latest(project_id)
    if not state or not state.video:
        raise HTTPException(404, f"no video for {project_id}")
    tracks = []
    for lang in state.video.subtitle_languages or []:
        vtt = project_dir(project_id) / "subtitles" / f"{lang.lower()}.vtt"
        if vtt.exists():
            tracks.append({"language": lang, "code": iso639_1(lang),
                           "url": asset_url(vtt),
                           "burned_in": lang == state.video.burned_subtitle_language})
    return tracks


@router.get("/film/{project_id}", dependencies=[Depends(require_project)])
def film(project_id: str):
    """Everything the player needs, as URLs rather than file paths.

    The state endpoint returns raw paths (Windows ones, on this machine), and
    the old page split those strings in the browser to guess an asset URL —
    which stops working the moment assets are served from a bucket.
    """
    state = sm.latest(project_id)
    if not state or not state.video or not state.video.final_video_path:
        raise HTTPException(404, f"no film yet for {project_id}")
    video = state.video
    speed = video.speed_factor or 1.0
    manifest = state.audio.manifest if state.audio else None
    timings = {s.scene_id: s for s in (manifest.scenes if manifest else [])}
    previews = {f.scene_id: f.preview_path
                for f in (state.storyboard.frames if state.storyboard else [])}
    stills = {f.scene_id: f.image_path for f in (video.frames or [])}

    # Which cut this is, counting from the first render (the storyboard is not a cut).
    rows = sm.history(project_id)
    first = next((i for i, r in enumerate(rows) if r.get("description") in RENDERS), None)
    cut = (len(rows) - first) if first is not None else 1

    chapters = []
    for scene in (state.script.scenes if state.script else []):
        t = timings.get(scene.scene_id)
        chapters.append({
            "scene_id": scene.scene_id,
            "index": scene.index,
            "title": scene.title,
            "tone": getattr(scene, "tone", "") or "",
            # A sped-up film plays its timeline faster, so the chapter marks move.
            "start_ms": round(t.start_ms / speed) if t else None,
            "end_ms": round(t.end_ms / speed) if t else None,
            "poster_url": asset_url(previews.get(scene.scene_id)
                                    or stills.get(scene.scene_id)),
        })
    return {
        "project_id": project_id,
        "title": state.script.story.title if state.script else "",
        "logline": state.script.story.logline if state.script else "",
        "video_url": asset_url(video.final_video_path),
        "duration_ms": video.duration_ms,
        "width": video.width, "height": video.height, "fps": video.fps,
        "version": state.version,
        "cut": cut,
        "format": state.script.story.format if state.script else film_format.format_of_size(video.width, video.height),
        "style": (state.script.story.style if state.script else "") or "",
        # Scenes drawn with a placeholder because every image service was at its limit.
        "stand_in_scenes": stand_in.scenes_with_stand_ins(video.frames or []),
        "chapters": chapters,
        "subtitles": subtitle_tracks(project_id),
        "sound": sound_summary(state),
        "titles": state.script.story.graphics.model_dump() if state.script else None,
        "effects": {s.scene_id: s.effect.model_dump() for s in state.script.scenes if s.effect} if state.script else {},
    }


def sound_summary(state) -> dict:
    """The film's mix as the Sound panel shows it: levels as a percent of how it was made, what is
    playing, and the creator's uploads (DST-39)."""
    audio = state.audio
    if not audio:
        return {}
    return {
        "music_on": audio.bgm_enabled,
        "music_pct": round(audio.music_volume / 0.32 * 100),
        "voices_pct": round(audio.voice_volume * 100),
        "ambience_pct": round(audio.ambience_volume / 0.22 * 100),
        "music_track": audio.music_track,
        "scene_music": dict(audio.scene_music),
        "moods": {s.scene_id: s.music_mood for s in state.script.scenes} if state.script else {},
        "ambience": dict(audio.ambience),
        "uploads": [{"id": f"upload:{u.id}", "title": u.title, "seconds": u.duration_s} for u in audio.uploads],
    }


@router.get("/languages")
def subtitle_languages():
    """Subtitle languages the pipeline can translate + embed."""
    return supported_names()


@router.get("/state/{project_id}", dependencies=[Depends(require_project)])
def get_state(project_id: str):
    state = sm.latest(project_id)
    if not state:
        raise HTTPException(404, f"project {project_id} not found")
    return state.model_dump(mode="json")


@router.get("/status/{project_id}", dependencies=[Depends(require_project)])
def get_status(project_id: str):
    """Lightweight status — job state, phase progress and the last event."""
    return progress.snapshot(project_id) or {"project_id": project_id, "status": "unknown"}
