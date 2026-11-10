"""High-level pipeline orchestrator.

Composes Phase 1 -> 2 -> 3 with progress events suitable for SSE/WebSocket
streaming, and snapshots the final state via StateManager.
"""
from __future__ import annotations
import contextlib
import functools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from agents.audio_agent import AudioAgent
from agents.edit_agent import EditAgent
from agents.edit_agent.describe import STEP_WORDS, describe
from agents.story_agent import StoryAgent, current_length_check
from agents.video_agent import VideoAgent
from shared import film_format, progress_hook, providers, stand_in, video_quality
from shared.assets import asset_url
from shared.constants import DEFAULT_FPS, film_length_tolerance

from .storyboard_view import cast, cast_note, image_budget, scene_cards
from shared.schemas.edit import EditCommand, EditResult
from shared.schemas.pipeline import PipelineState
from shared.timeline import LINE_GAP_MS, SCENE_PREROLL_MS, SCENE_TAIL_MS, estimate_line_ms
from shared.utils.ids import new_project_id
from shared.utils.logging import get_logger
from state_manager.snapshot import referenced_files
from state_manager.state_manager import StateManager

from .graph import PipelineGraph
from .state import RunContext

log = get_logger("orchestrator")


class EditFailed(RuntimeError):
    """An edit that could not be applied; the film is left as it was."""


def _noting_daily_quotas(method):
    """Record every daily-quota refusal made while this job runs, for its
    completion event (DST-20). Model calls note them as they happen."""
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        with providers.noting_daily_quotas():
            return method(*args, **kwargs)
    return wrapper


# Which phase each reported step belongs to, for the progress event it becomes.
STEP_PHASE = {"voices": "audio", "portraits": "video", "pictures": "video",
              "shots": "video", "cut": "video"}


# What a report is called while a finished film is being changed (the edit's own words).
EDIT_STEP_WORDS = {"shots": "Drawing the shots", "looks": "Drawing the look over the scenes",
                   "cut": "Cutting the film"}


@contextlib.contextmanager
def _following_steps(emit, project_id, editing=False):
    """Turn the agents' step reports into progress events while a job runs, so the
    creator sees "shots 7 of 12" rather than one sentence for four minutes (DST-38).

    While a finished film is being changed (`editing`) the events are the edit's own
    (phase "edit", in words), and Stop lands between them: every event, and every `pulse()` from
    work that runs long between events, checks whether the job was cancelled (DST-41)."""
    def sink(step, done, total):
        payload = {"step": step, "done": done, "total": total}
        if editing:
            words = EDIT_STEP_WORDS.get(step, "Working on it")
            emit(ProgressEvent(phase="edit", status="step", project_id=project_id,
                               message=f"{words} ({done} of {total})" if total > 1 else words,
                               progress=0.0, payload=payload))
            return
        emit(ProgressEvent(phase=STEP_PHASE.get(step, "video"), status="step",
                           project_id=project_id, message=step, progress=0.0, payload=payload))
    token = progress_hook.listen(sink, getattr(emit, "check_cancelled", None),
                                 getattr(emit, "peek_cancelled", None))
    try:
        yield
    finally:
        progress_hook.stop(token)


# How a version that produced a film is described in the history. The film's
# version list starts at the first of these.
INITIAL_RUN = "initial pipeline run"
RENDERED = "rendered from storyboard"
RENDER_DESCRIPTIONS = (INITIAL_RUN, RENDERED)


@dataclass
class ProgressEvent:
    """Streamed to clients to drive the per-phase progress UI."""
    phase: str               # "story" | "audio" | "video" | "complete" | "error"
    status: str              # "started" | "complete" | "failed"
    message: str = ""
    progress: float = 0.0    # 0.0 - 1.0
    project_id: str = ""
    payload: dict = field(default_factory=dict)


class PipelineOrchestrator:
    def __init__(self, state_manager: Optional[StateManager] = None):
        self.sm = state_manager or StateManager()
        self.story = StoryAgent()
        self.audio = AudioAgent()
        self.video = VideoAgent()
        self.editor = EditAgent(self.sm)

    # ---- public ----------------------------------------------------------

    @_noting_daily_quotas
    def run_full(
        self,
        prompt: str,
        on_event: Optional[Callable[[ProgressEvent], None]] = None,
        target_duration_s: int = 45,
        scene_count: int = 4,
        with_bgm: bool = True,
        with_subtitles: bool = True,
        subtitle_language: str = "English",
        burn_subtitles: bool = True,
        tts_engine: Optional[str] = None,
        project_id: Optional[str] = None,
        use_text_to_video: Optional[bool] = None,
        use_lip_sync: Optional[bool] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: int = DEFAULT_FPS,
        format: str = film_format.DEFAULT_FORMAT,
        style: str = film_format.DEFAULT_STYLE,
    ) -> PipelineState:
        project_id = project_id or new_project_id()
        state = PipelineState(project_id=project_id, user_prompt=prompt)
        ctx = RunContext(
            state=state,
            target_duration_s=target_duration_s,
            scene_count=scene_count,
            with_bgm=with_bgm,
            with_subtitles=with_subtitles,
            subtitle_language=subtitle_language,
            burn_subtitles=burn_subtitles,
            tts_engine=tts_engine,
            use_text_to_video=use_text_to_video,
            use_lip_sync=use_lip_sync,
            width=width, height=height, fps=fps,
            format=format, style=style,
        )

        graph = self._build_graph()
        emit = on_event or (lambda _e: None)

        emit(ProgressEvent(phase="story", status="started", project_id=project_id,
                           message="Generating story, characters, and scenes",
                           progress=0.05))
        try:
            graph.run(ctx,
                      on_node=lambda name, _c: emit(self._node_event(name, project_id, done=False)),
                      on_node_done=lambda name, _c: emit(self._node_event(name, project_id, done=True)))
        except Exception as e:  # noqa: BLE001
            emit(ProgressEvent(phase="error", status="failed", project_id=project_id,
                               message=str(e), progress=1.0))
            raise

        # Final snapshot.
        state.stage = "rendered"
        version = self.sm.snapshot(
            state,
            asset_paths=self._collect_assets(state),
            description=INITIAL_RUN,
        )
        note, notes = self._completion_notes(state)
        emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                           message=f"Pipeline finished (version {version.version}){note}",
                           progress=1.0,
                           payload={"version": version.version,
                                    "video_path": state.video.final_video_path
                                    if state.video else None, **notes}))
        return state

    # ---- storyboard: plan cheaply, approve, then render ------------------

    @_noting_daily_quotas
    def plan(
        self,
        prompt: str,
        on_event: Optional[Callable[[ProgressEvent], None]] = None,
        target_duration_s: int = 45,
        scene_count: int = 4,
        project_id: Optional[str] = None,
        with_preview: bool = True,
        # The picture size, not a thumbnail: a preview drawn at the size the render
        # draws its pictures *is* the scene's wide shot, so the render reuses it
        # instead of paying for the identical image again. At 512x288 it never
        # matched. None: the size for a film of the default size (the film itself
        # is bigger; the camera enlarges every picture, config/video.yaml).
        preview_width: Optional[int] = None,
        preview_height: Optional[int] = None,
        format: str = film_format.DEFAULT_FORMAT,
        style: str = film_format.DEFAULT_STYLE,
    ) -> PipelineState:
        """Write the script and draw one preview image per scene.

        This is the cheap half of the pipeline: no voices, no video. The result
        is a storyboard to review and edit before `render` spends the rest.
        """
        project_id = project_id or new_project_id()
        state = PipelineState(project_id=project_id, user_prompt=prompt)
        emit = on_event or (lambda _e: None)
        emit(ProgressEvent(phase="story", status="started", project_id=project_id,
                           message="Writing the script", progress=0.1))
        try:
            self.story.run(state, target_duration_s=target_duration_s, scene_count=scene_count,
                           format=format, style=style)
            emit(ProgressEvent(phase="story", status="complete", project_id=project_id,
                               message="Script ready", progress=0.4))
            # The script exists long before any picture does. Sending it now
            # lets the interface show every scene, its tone and its lines while
            # the frames are still being drawn.
            script = state.script
            emit(ProgressEvent(
                phase="storyboard", status="script", project_id=project_id,
                message=f"{len(script.scenes)} scenes written", progress=0.45,
                payload={"prompt": state.user_prompt,
                         "title": script.story.title, "logline": script.story.logline,
                         "scenes": scene_cards(script), "cast": cast(script),
                         "cast_note": cast_note(state),
                         "images": image_budget(script)}))
            if with_preview:
                emit(ProgressEvent(phase="storyboard", status="started", project_id=project_id,
                                   message="Drawing storyboard previews", progress=0.5))
                drawn = []
                # The portraits and the previews share one progress bar.
                total = len(script.scenes) + len(script.characters.characters)
                # Creators count scenes from one; models sometimes number them
                # from zero, so the position is used rather than scene.index.
                position = {s.scene_id: n for n, s in enumerate(script.scenes, start=1)}

                def frame_ready(scene, path):
                    drawn.append(scene.scene_id)
                    emit(ProgressEvent(
                        phase="storyboard", status="frame", project_id=project_id,
                        message=f"Scene {position[scene.scene_id]} drawn: {scene.title}",
                        progress=0.5 + 0.45 * len(drawn) / max(1, total),
                        payload={"scene_id": scene.scene_id,
                                 "preview_url": asset_url(path, versioned=True)}))

                def portrait_ready(character, path, ok):
                    drawn.append(character.id)
                    emit(ProgressEvent(
                        phase="storyboard", status="portrait", project_id=project_id,
                        message=f"Portrait drawn: {character.name}",
                        progress=0.5 + 0.45 * len(drawn) / max(1, total),
                        payload={"character_id": character.id, "drawn": ok,
                                 "portrait_url": asset_url(path, versioned=True)}))

                self.video.generate_storyboard(state, width=preview_width,
                                               height=preview_height,
                                               on_frame=frame_ready,
                                               on_portrait=portrait_ready)
            state.stage = "storyboard"
        except Exception as e:  # noqa: BLE001
            emit(ProgressEvent(phase="error", status="failed", project_id=project_id,
                               message=str(e), progress=1.0))
            raise

        version = self.sm.snapshot(state, asset_paths=self._collect_assets(state),
                                   description="storyboard")
        note, notes = self._completion_notes(state)
        emit(ProgressEvent(phase="storyboard", status="complete", project_id=project_id,
                           message=f"Storyboard ready: {len(state.script.scenes)} scenes{note}",
                           progress=1.0,
                           payload={"version": version.version,
                                    "scenes": len(state.script.scenes),
                                    # Recorded with the job's result (DST-34).
                                    "format": state.script.story.format,
                                    "style": state.script.story.style, **notes}))
        return state

    def update_storyboard(self, project_id: str, scene_id: str,
                          title: Optional[str] = None, setting: Optional[str] = None,
                          visual_prompt: Optional[str] = None,
                          dialogue: Optional[Dict[str, str]] = None,
                          regenerate_preview: bool = True,
                          outcome: Optional[Dict[str, Any]] = None) -> PipelineState:
        """Edit one storyboard scene (and redraw its preview if the visuals changed).

        The text is always saved, as a new version. If the picture could not be
        redrawn (every image provider failed, or only the offline placeholder
        answered), the previous picture and its signature are kept and the render
        will draw the scene from the new text. A caller that passes `outcome` gets
        {"redraw": {"scene_id": ..., "ok": bool}} when a redraw was asked for.
        """
        state = self.sm.latest(project_id)
        if not state or not state.script:
            raise ValueError(f"no storyboard for {project_id}")
        scene = next((s for s in state.script.scenes if s.scene_id == scene_id), None)
        if not scene:
            raise ValueError(f"unknown scene '{scene_id}'")

        visual_changed = visual_prompt is not None and visual_prompt != scene.visual_prompt
        if title is not None:
            scene.title = title
        if setting is not None:
            scene.setting = setting
        if visual_changed:
            scene.visual_prompt = visual_prompt
        for line_id, text in (dialogue or {}).items():
            line = next((ln for ln in scene.dialogue if ln.line_id == line_id), None)
            if not line:
                raise ValueError(f"unknown line '{line_id}' in {scene_id}")
            line.text = text
            line.duration_ms = estimate_line_ms(text)
        if dialogue:
            # Keep the scene's estimated length in step with its new lines.
            scene.duration_ms = (SCENE_PREROLL_MS + SCENE_TAIL_MS
                                 + sum(ln.duration_ms for ln in scene.dialogue)
                                 + LINE_GAP_MS * max(0, len(scene.dialogue) - 1))

        self.story.serialize(state)
        board = state.storyboard
        redraw = visual_changed and regenerate_preview
        failed: List[str] = []
        self.video.generate_storyboard(
            state,
            width=board.preview_width if board else None,
            height=board.preview_height if board else None,
            scene_ids=[scene_id] if redraw else [],
            failed=failed,
            character_ids=[],        # a scene edit never redraws the cast
        )
        redrawn = redraw and scene_id not in failed
        if redraw and outcome is not None:
            outcome["redraw"] = {"scene_id": scene_id, "ok": redrawn}
        self.sm.snapshot(
            state, asset_paths=self._collect_assets(state),
            description=f"storyboard edit: {scene_id}"
                        + (" (picture not redrawn)" if redraw and not redrawn else ""))
        return state

    @_noting_daily_quotas
    def render(
        self,
        project_id: str,
        on_event: Optional[Callable[[ProgressEvent], None]] = None,
        with_bgm: bool = True,
        with_subtitles: bool = True,
        subtitle_language: str = "English",
        burn_subtitles: bool = True,
        tts_engine: Optional[str] = None,
        use_text_to_video: Optional[bool] = None,
        use_lip_sync: Optional[bool] = None,
    ) -> PipelineState:
        """Render an approved storyboard into the finished film."""
        state = self.sm.latest(project_id)
        if not state or not state.script:
            raise ValueError(f"no storyboard to render for {project_id}")
        emit = on_event or (lambda _e: None)
        ctx = RunContext(state=state, format=state.script.story.format,
                         style=state.script.story.style or film_format.DEFAULT_STYLE,
                         with_bgm=with_bgm, with_subtitles=with_subtitles,
                         subtitle_language=subtitle_language, burn_subtitles=burn_subtitles,
                         tts_engine=tts_engine,
                         use_text_to_video=use_text_to_video, use_lip_sync=use_lip_sync)
        try:
            with _following_steps(emit, project_id):
                self._render_graph().run(
                    ctx,
                    on_node=lambda name, _c: emit(self._node_event(name, project_id, done=False)),
                    on_node_done=lambda name, _c: emit(self._node_event(name, project_id, done=True)),
                )
        except Exception as e:  # noqa: BLE001
            emit(ProgressEvent(phase="error", status="failed", project_id=project_id,
                               message=str(e), progress=1.0))
            raise

        if state.storyboard:
            state.storyboard.approved_at = datetime.utcnow().isoformat()
        state.stage = "rendered"
        version = self.sm.snapshot(state, asset_paths=self._collect_assets(state),
                                   description=RENDERED)
        note, notes = self._completion_notes(state)
        emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                           message=f"Film rendered (version {version.version}){note}",
                           progress=1.0,
                           payload={"version": version.version,
                                    "video_path": state.video.final_video_path
                                    if state.video else None, **notes}))
        return state

    # ---- generator variant for SSE/WebSocket streaming ------------------

    def stream_full(self, prompt: str, **kwargs) -> Iterator[ProgressEvent]:
        events: List[ProgressEvent] = []

        def collector(ev: ProgressEvent) -> None:
            events.append(ev)

        # We can't yield from inside on_event because run_full is synchronous;
        # the FastAPI WebSocket route will call run_full with its own callback.
        self.run_full(prompt, on_event=collector, **kwargs)
        for ev in events:
            yield ev

    @_noting_daily_quotas
    def re_run_phase(self, project_id: str, phase: str,
                     on_event: Optional[Callable[[ProgressEvent], None]] = None) -> PipelineState:
        state = self.sm.latest(project_id)
        if not state:
            raise ValueError(f"no project state for {project_id}")
        if phase not in ("story", "audio", "video"):
            raise ValueError(f"unknown phase '{phase}'")
        emit = on_event or (lambda _e: None)

        # Re-runs keep the project's existing settings.
        v, a = state.video, state.audio
        with_bgm = a.bgm_enabled if a else True
        # Re-runs keep the voice engine the film was recorded with.
        tts_engine = a.voice_configs[0].engine if (a and a.voice_configs) else None
        video_kwargs = dict(
            with_subtitles=v.has_subtitles if v else True,
            subtitle_language=v.subtitle_language if v else "English",
            burn_subtitles=v.burn_subtitles if v else True,
            width=v.width if v else film_format.film_size(self._spec_kwargs(state)["format"])[0],
            height=v.height if v else film_format.film_size(self._spec_kwargs(state)["format"])[1],
            fps=v.fps if v else 24,
            use_text_to_video=v.used_text_to_video if v else None,
            use_lip_sync=v.used_lip_sync if v else None,
            cinematic_post=v.cinematic_post if v else True,
        )
        steps = ["story", "audio", "video"][["story", "audio", "video"].index(phase):]
        for i, step in enumerate(steps):
            emit(ProgressEvent(phase=step, status="started", project_id=project_id,
                               message=f"Re-running {step}", progress=0.1 + 0.8 * i / len(steps)))
            if step == "story":
                self.story.run(state, target_duration_s=state.script.story.target_duration_s
                               if state.script else 45,
                               scene_count=len(state.script.scenes) if state.script else 4,
                               **self._spec_kwargs(state))
            elif step == "audio":
                self.audio.run(state, with_bgm=with_bgm, tts_engine=tts_engine)
            else:
                self.video.run(state, **video_kwargs)
            emit(ProgressEvent(phase=step, status="complete", project_id=project_id,
                               message=f"{step.capitalize()} regenerated",
                               progress=0.1 + 0.8 * (i + 1) / len(steps)))
        version = self.sm.snapshot(
            state,
            asset_paths=self._collect_assets(state),
            description=f"re-run phase: {phase}",
        )
        note, notes = self._completion_notes(state)
        emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                           message=f"Phase {phase} re-run, version {version.version}{note}",
                           progress=1.0,
                           payload={"version": version.version, **notes}))
        return state

    @_noting_daily_quotas
    def respec(self, project_id: str, format: str, style: str,
               on_event: Optional[Callable[[ProgressEvent], None]] = None) -> PipelineState:
        """Change a storyboard's format and/or style, and draw every picture again (DST-34).

        Only before the render. Previews and portraits are redrawn in the new look at the new
        shape, streamed like a plan's, and the script, the creator's edits and their shot choices
        are kept. A picture that could not be drawn keeps what it had and is named in the
        completion event: its saved signature no longer matches, so the render draws it fresh
        rather than reusing a picture from the old look.
        """
        emit = on_event or (lambda _e: None)
        format, style = film_format.check_format(format), film_format.check_style(style)
        state = self.sm.latest(project_id)
        if not state or not state.script:
            raise ValueError(f"no storyboard for {project_id}")
        if state.video and state.video.final_video_path:
            raise ValueError("this film is already rendered; its format and style are fixed")
        script = state.script
        script.story.format, script.story.style = format, style
        state.metadata["film_spec"] = {**(state.metadata.get("film_spec") or {}), "format": format,
                                       "style": style}
        width, height = video_quality.image_size(*film_format.film_size(format))
        if state.storyboard:
            state.storyboard.preview_width, state.storyboard.preview_height = width, height
        emit(ProgressEvent(
            phase="storyboard", status="script", project_id=project_id,
            message=f"Redrawing in {film_format.STYLES[style]['label'].lower()}, {format}", progress=0.05,
            payload={"prompt": state.user_prompt, "title": script.story.title,
                     "logline": script.story.logline, "scenes": scene_cards(script),
                     "cast": cast(script, None, state), "cast_note": cast_note(state),
                     "images": image_budget(script), "format": format, "style": style}))
        emit(ProgressEvent(phase="storyboard", status="started", project_id=project_id,
                           message="Drawing the storyboard again", progress=0.1))
        drawn: List[str] = []
        failed: List[str] = []
        total = len(script.scenes) + len(script.characters.characters)
        position = {s.scene_id: n for n, s in enumerate(script.scenes, start=1)}

        def frame_ready(scene, path):
            drawn.append(scene.scene_id)
            emit(ProgressEvent(
                phase="storyboard", status="frame", project_id=project_id,
                message=f"Scene {position[scene.scene_id]} drawn: {scene.title}",
                progress=0.1 + 0.85 * len(drawn) / max(1, total),
                payload={"scene_id": scene.scene_id, "preview_url": asset_url(path, versioned=True)}))

        def portrait_ready(character, path, ok):
            drawn.append(character.id)
            emit(ProgressEvent(
                phase="storyboard", status="portrait", project_id=project_id,
                message=f"Portrait drawn: {character.name}",
                progress=0.1 + 0.85 * len(drawn) / max(1, total),
                payload={"character_id": character.id, "drawn": ok,
                         "portrait_url": asset_url(path, versioned=True)}))

        try:
            self.video.generate_storyboard(state, width=width, height=height, failed=failed,
                                           on_frame=frame_ready, on_portrait=portrait_ready)
        except Exception as e:  # noqa: BLE001
            emit(ProgressEvent(phase="error", status="failed", project_id=project_id,
                               message=str(e), progress=1.0))
            raise
        self.story.serialize(state)
        version = self.sm.snapshot(state, asset_paths=self._collect_assets(state),
                                   description=f"storyboard restyle: {format} {style}")
        note, notes = self._completion_notes(state)
        if failed:
            note += f" · {len(failed)} picture(s) could not be redrawn and are drawn when you render"
            notes["restyle_failed"] = failed
        emit(ProgressEvent(phase="storyboard", status="complete", project_id=project_id,
                           message=f"Storyboard redrawn: {format}, {style}{note}", progress=1.0,
                           payload={"version": version.version, "scenes": len(script.scenes),
                                    "format": format, "style": style, **notes}))
        return state

    @staticmethod
    def _spec_kwargs(state: PipelineState) -> Dict[str, str]:
        """The creator's format and style, for a step that writes the script again."""
        story = state.script.story if state.script else None
        return {"format": story.format if story else film_format.DEFAULT_FORMAT,
                "style": (story.style if story else "") or film_format.DEFAULT_STYLE}

    # ---- edit and undo (jobs, like everything else that writes a version) --

    def apply_op(self, project_id: str, op: str, params: Dict[str, Any],
                 on_event: Optional[Callable[[ProgressEvent], None]] = None) -> PipelineState:
        """Apply one of the shared editing operations (agents/edit_agent/ops.py), DST-39.

        Before a render it is a storyboard edit: the script changes, a new version is saved,
        nothing is drawn. After one, the film is cut again from the same pictures and only the
        scenes whose plan changed are re-rendered, as a job like any change to a finished film.
        Either way the creator's choice is stored on the script, so later edits keep it.
        """
        from agents.edit_agent import ops

        emit = on_event or (lambda _e: None)
        state = self.sm.latest(project_id)
        if not state or not state.script:
            raise ValueError(f"no storyboard for {project_id}")
        label = ops.check(state, op, params)          # raises OpError (a ValueError) before anything changes
        film = state.video is not None and bool(state.video.final_video_path)
        if film:
            emit(ProgressEvent(phase="edit", status="started", project_id=project_id,
                               message="Changing the film", progress=0.05,
                               payload={"query": label}))

            def hear(kind, info):
                if kind == "understood":
                    emit(ProgressEvent(phase="edit", status="understood", project_id=project_id,
                                       message=f"Understood: {label}", progress=0.1,
                                       payload={**info, "summary": label}))
                elif kind == "step":
                    emit(ProgressEvent(
                        phase="edit", status="step", project_id=project_id,
                        message=STEP_WORDS.get(info["name"], "Working on it"),
                        progress=0.1 + 0.85 * info["index"] / max(info["total"], 1),
                        payload=info))

            with _following_steps(emit, project_id, editing=True):
                result = self.editor.apply_op(project_id, op, params, on_progress=hear)
            if not result.success:
                raise EditFailed(result.error or "the change could not be made")
            emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                               message=f"{label} (version {result.new_version})", progress=1.0,
                               payload={"version": result.new_version, "kind": "edit",
                                        "result": result.model_dump(mode="json")}))
            return self.sm.latest(project_id)

        ops.apply(state, op, params)
        self.story.serialize(state)
        self.sm.snapshot(state, asset_paths=self._collect_assets(state),
                         description=f"storyboard edit: {op}",
                         edit_intent={"intent": op, "parameters": dict(params), "query": label})
        emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                           message=label, progress=1.0,
                           payload={"version": state.version, "kind": "shot"}))
        return state

    def apply_ops(self, project_id: str, items: List[Dict[str, Any]],
                  on_event: Optional[Callable[[ProgressEvent], None]] = None) -> PipelineState:
        """Apply a stacked list of editing operations at once (DST-40): one job, one version.

        On a finished film the film is cut once for all of them. Before a render each is a
        storyboard edit and nothing is drawn, saved as one version."""
        from agents.edit_agent import ops

        emit = on_event or (lambda _e: None)
        state = self.sm.latest(project_id)
        if not state or not state.script:
            raise ValueError(f"no storyboard for {project_id}")
        labels = ops.check_many(state, items)            # raises OpError before anything changes
        label = labels[0] if len(labels) == 1 else f"{len(labels)} changes: " + "; ".join(labels)
        film = state.video is not None and bool(state.video.final_video_path)
        if film:
            emit(ProgressEvent(phase="edit", status="started", project_id=project_id,
                               message="Applying the changes", progress=0.05, payload={"query": label}))

            def hear(kind, info):
                if kind == "understood":
                    emit(ProgressEvent(phase="edit", status="understood", project_id=project_id,
                                       message=f"Understood: {label}", progress=0.1,
                                       payload={**info, "summary": label}))
                elif kind == "step":
                    emit(ProgressEvent(
                        phase="edit", status="step", project_id=project_id,
                        message=STEP_WORDS.get(info["name"], "Working on it"),
                        progress=0.1 + 0.85 * info["index"] / max(info["total"], 1), payload=info))

            with _following_steps(emit, project_id, editing=True):
                result = self.editor.apply_ops(project_id, items, on_progress=hear)
            if not result.success:
                raise EditFailed(result.error or "the changes could not be made")
            emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                               message=f"{label} (version {result.new_version})", progress=1.0,
                               payload={"version": result.new_version, "kind": "edit",
                                        "result": result.model_dump(mode="json")}))
            return self.sm.latest(project_id)

        for it in items:
            ops.apply(state, it["op"], it.get("params") or {})
        self.story.serialize(state)
        self.sm.snapshot(state, asset_paths=self._collect_assets(state),
                         description=f"storyboard edit: {len(items)} changes",
                         edit_intent={"intent": "batch", "parameters": {"ops": items}, "query": label[:400]})
        emit(ProgressEvent(phase="complete", status="complete", project_id=project_id, message=label,
                           progress=1.0, payload={"version": state.version, "kind": "shot"}))
        return state

    def set_shot(self, project_id: str, scene_id: str, shot_id: str,
                 move: Optional[str] = None, framing: Optional[str] = None,
                 reset: bool = False,
                 on_event: Optional[Callable[[ProgressEvent], None]] = None) -> PipelineState:
        """Change one shot's camera move or framing (DST-38, F4): the `set_camera` operation."""
        return self.apply_op(project_id, "set_camera",
                             {"scene": scene_id, "shot_id": shot_id, "move": move,
                              "framing": framing, "reset": reset}, on_event)

    @_noting_daily_quotas
    def edit(self, project_id: str, query: str,
             on_event: Optional[Callable[[ProgressEvent], None]] = None,
             user_id: Optional[str] = None) -> EditResult:
        """Apply one natural-language edit to a rendered film, as a new version."""
        emit = on_event or (lambda _e: None)
        state = self.sm.latest(project_id)
        names = ({c.id: c.name for c in state.script.characters.characters}
                 if state and state.script else {})
        emit(ProgressEvent(phase="edit", status="started", project_id=project_id,
                           message="Reading your change", progress=0.05,
                           payload={"query": query}))

        def progress(kind: str, info: dict) -> None:
            if kind == "understood":
                summary = describe(info["intent"], names)
                emit(ProgressEvent(phase="edit", status="understood", project_id=project_id,
                                   message=f"Understood: {summary}", progress=0.1,
                                   payload={**info, "summary": summary}))
            else:
                emit(ProgressEvent(phase="edit", status="step", project_id=project_id,
                                   message=STEP_WORDS.get(info["name"], "Working on it"),
                                   progress=0.1 + 0.85 * info["index"] / max(info["total"], 1),
                                   payload=info))

        with _following_steps(emit, project_id, editing=True):
            result = self.editor.edit(EditCommand(project_id=project_id, query=query,
                                                  user_id=user_id), on_progress=progress)
        if not result.success:
            raise EditFailed(result.error or "the edit could not be applied")
        note, notes = self._completion_notes(self.sm.latest(project_id))
        if result.not_done:
            notes = {**notes, "not_done": result.not_done}
            note += " · not done: " + "; ".join(result.not_done)
        if result.notes:
            notes = {**notes, "edit_notes": result.notes}
            note += " · " + "; ".join(result.notes)
        emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                           message=f"Edit applied (version {result.new_version}){note}",
                           progress=1.0,
                           payload={"version": result.new_version, "kind": "edit",
                                    "result": result.model_dump(mode="json"), **notes}))
        return result

    def revert(self, project_id: str, version: int,
               on_event: Optional[Callable[[ProgressEvent], None]] = None) -> PipelineState:
        """Go back to an earlier version. History stays linear: this is a new version."""
        emit = on_event or (lambda _e: None)
        emit(ProgressEvent(phase="revert", status="started", project_id=project_id,
                           message=f"Going back to version {version}", progress=0.2))
        state = self.sm.revert(project_id, int(version))
        emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                           message=f"Back to version {version}, saved as version {state.version}",
                           progress=1.0,
                           payload={"version": state.version, "restored": int(version),
                                    "kind": "revert"}))
        return state

    # ---- graph wiring ----------------------------------------------------

    def _build_graph(self) -> PipelineGraph:
        g = PipelineGraph()
        g.add("phase1_story",
              lambda c: self.story.run(c.state, target_duration_s=c.target_duration_s,
                                       scene_count=c.scene_count,
                                       format=c.format, style=c.style),
              next_=["phase2_audio"], entry=True)
        g.add("phase2_audio",
              lambda c: self.audio.run(c.state, with_bgm=c.with_bgm,
                                       tts_engine=c.tts_engine),
              next_=["phase3_video"])
        g.add("phase3_video",
              lambda c: self.video.run(
                  c.state, with_subtitles=c.with_subtitles,
                  subtitle_language=c.subtitle_language,
                  width=c.film_size()[0], height=c.film_size()[1], fps=c.fps,
                  use_text_to_video=c.use_text_to_video,
                  use_lip_sync=c.use_lip_sync,
              ),
              next_=[])
        return g

    def _render_graph(self) -> PipelineGraph:
        """Phases 2-3 only — used when rendering an approved storyboard."""
        g = PipelineGraph()
        g.add("phase2_audio",
              lambda c: self.audio.run(c.state, with_bgm=c.with_bgm,
                                       tts_engine=c.tts_engine),
              next_=["phase3_video"], entry=True)
        g.add("phase3_video",
              lambda c: self.video.run(
                  c.state, with_subtitles=c.with_subtitles,
                  subtitle_language=c.subtitle_language,
                  width=c.film_size()[0], height=c.film_size()[1], fps=c.fps,
                  use_text_to_video=c.use_text_to_video,
                  use_lip_sync=c.use_lip_sync,
                  burn_subtitles=c.burn_subtitles,
              ),
              next_=[])
        return g

    @staticmethod
    def _node_event(name: str, project_id: str, done: bool) -> ProgressEvent:
        # (phase, started message, started progress, done message, done progress)
        mapping = {
            "phase1_story": ("story", "Generating story + characters", 0.10,
                             "Story, characters and scenes ready", 0.30),
            "phase2_audio": ("audio", "Synthesizing voices + BGM", 0.35,
                             "Voices, music and timeline ready", 0.55),
            "phase3_video": ("video", "Generating images + composing video", 0.60,
                             "Video composed", 0.95),
        }
        phase, start_msg, start_p, done_msg, done_p = mapping.get(
            name, (name, name, 0.5, name, 0.5))
        return ProgressEvent(phase=phase, status="complete" if done else "started",
                             project_id=project_id,
                             message=done_msg if done else start_msg,
                             progress=done_p if done else start_p)

    @staticmethod
    def _completion_notes(state: Optional[PipelineState]) -> Tuple[str, Dict[str, Any]]:
        """Notes for the completion message, and payload, when the film is finished
        but not as asked, so the job says so rather than report a plain success:
        a provider whose daily quota ran out (DST-20, read from the job's record,
        see `_noting_daily_quotas`), a subtitle language that could not be
        translated (DST-18), or a length off the target (DST-17 and DST-28,
        FILM_LENGTH_TOLERANCE): a finished film's, or a storyboard script's
        that the repairs could not bring inside it."""
        note, payload = "", {}
        used_up = providers.daily_quotas_used_up()
        if used_up:
            note += f" · daily quota used up, the next provider took over: {', '.join(used_up)}"
            payload["daily_quota_used_up"] = used_up
        video = state.video if state else None
        if not video:
            # A storyboard has no film yet, so what it can say is the script's
            # planned length (DST-28), once the repairs have had their turn. After
            # a render the finished film's own length is the one that counts.
            check = current_length_check(state) if state else None
            if check and not check["within"]:
                note += (f" · the script plans {check['planned_s']:.1f} s for a "
                         f"{check['target_s']} s target")
                payload["script_length_warning"] = {
                    k: check[k] for k in ("planned_s", "target_s", "tolerance", "repairs")}
            sc = state.metadata.get("scene_check")
            if sc and not sc.get("within", True):
                note += f" · the script has {sc['got']} scenes for the {sc['asked']} asked"
                payload["scene_count_warning"] = {k: sc[k] for k in ("asked", "got", "repairs", "merged")}
            said = cast_note(state)
            if said:
                note += f" · {said}"
                payload["cast_removed"] = [
                    r["name"] for r in state.metadata["cast_check"]["removed"]
                    if r.get("named_in_prompt")]
            return note, payload
        stand_ins = stand_in.scenes_with_stand_ins(video.frames)
        if stand_ins:
            numbers = {s.scene_id: i + 1 for i, s in enumerate(state.script.scenes)}
            note += (" · stand-in pictures in scene "
                     + ", ".join(str(numbers.get(sid, sid)) for sid in stand_ins))
            payload["stand_in_scenes"] = stand_ins
        if video.subtitles_skipped:
            note += f" · subtitles skipped, translation failed: {', '.join(video.subtitles_skipped)}"
            payload["subtitles_skipped"] = list(video.subtitles_skipped)
        target_s = state.script.story.target_duration_s if state.script else 0
        film_s = video.duration_ms / 1000
        tolerance = film_length_tolerance()
        if target_s and film_s and abs(film_s - target_s) > tolerance * target_s:
            note += f" · length off target: {film_s:.1f} s for a {target_s} s target"
            payload["length_warning"] = {"film_s": round(film_s, 1), "target_s": target_s,
                                         "tolerance": tolerance}
            log.warning("film %s is %.1f s for a %d s target (more than %g%% off)",
                        state.project_id, film_s, target_s, tolerance * 100)
        return note, payload

    @staticmethod
    def _collect_assets(state: PipelineState) -> List[str]:
        return referenced_files(state)
