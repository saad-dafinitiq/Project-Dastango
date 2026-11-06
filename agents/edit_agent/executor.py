"""Edit step executor — runs the targeted re-runs implied by an EditStep.

Edits reuse the same building blocks as a fresh pipeline run, so an edited
film is built exactly like a new one:

    audio edits  -> AudioAgent.render_line / retime / remix
    image edits  -> change shot-bank images or portraits in place
    every edit   -> VideoAgent.compose (re-cuts to the timeline, re-renders only
                    scenes whose shots changed, re-embeds subtitles)

Each handler returns the asset paths it created/modified; the EditAgent then
snapshots the whole state for undo.
"""
from __future__ import annotations
from typing import Dict, List

from agents.audio_agent import AudioAgent
from agents.story_agent.appearance import build_appearance_lock, character_seed
from agents.video_agent import VideoAgent
from mcp.tool_executor import ToolExecutor
from shared import film_format, video_quality
from shared.schemas.audio import VoiceConfig
from shared.schemas.pipeline import PipelineState
from shared.utils.files import project_dir
from shared.utils.logging import get_logger

from . import ops
from .planner import EditStep

log = get_logger("edit_executor")

MIN_SPEED, MAX_SPEED = 0.25, 4.0


class EditExecutor:
    """Carries out the re-runs implied by an edit step."""

    def __init__(self):
        self.tools = ToolExecutor()
        self.audio = AudioAgent()
        self.video = VideoAgent()

    # ---- entry -----------------------------------------------------------

    def execute(self, state: PipelineState, step: EditStep) -> List[str]:
        log.info("executing step %s (%s/%s) params=%s",
                 step.name, step.target, step.scope, step.params)
        if step.name in ops.OPS:
            # The shared editing operations (DST-39): the same code whether a click or a
            # sentence asked for it. They change the script; the steps after them cut the film.
            ops.apply(state, step.name, step.params)
            return []
        handler = getattr(self, f"_step_{step.name}", None)
        if not handler:
            raise ValueError(f"no handler for step '{step.name}'")
        return handler(state, step) or []

    # ---- audio handlers --------------------------------------------------

    def _step_rerun_audio(self, state: PipelineState, step: EditStep) -> List[str]:
        """Re-record the lines an edit touches, each in the voice it has now.

        Edits compose in the order they were made: a scene's own voice (from a
        scene-scoped edit) is kept in `audio.scene_voices`, and later character
        or global edits adjust it as well as the character's voice — so "make
        scene 2 whisper" then "make everyone louder" leaves scene 2 whispering,
        louder.
        """
        voice_params = self.audio.normalise_voice_params(
            {k: v for k, v in (step.params or {}).items() if k in ("tone", "volume", "voice")})
        audio = state.audio
        if not audio or self._script_lines_changed(state):
            # The lines themselves are new (the script was rewritten), so there
            # is nothing to keep but the engine the film was recorded with.
            engine = audio.voice_configs[0].engine if audio and audio.voice_configs else None
            self.audio.run(state, with_bgm=audio.bgm_enabled if audio else True,
                           tts_engine=engine)
            return list(state.phase2.artifact_paths)

        characters = {c.id: c for c in state.script.characters.characters}
        base: Dict[str, VoiceConfig] = {v.character_id: v for v in audio.voice_configs}
        lines = [s for s in audio.manifest.segments if s.kind == "dialogue"]
        if step.scope.startswith("character:"):
            char_id = step.scope.split(":", 1)[1]
            if char_id not in base:
                raise ValueError(f"unknown character '{char_id}'")
            targets = [s for s in lines if s.character_id == char_id]
            voices = [base[char_id]] + [own[char_id] for own in audio.scene_voices.values()
                                        if char_id in own]
        elif step.scope.startswith("scene:"):
            scene_id = step.scope.split(":", 1)[1]
            targets = [s for s in lines if s.scene_id == scene_id]
            if not targets:
                raise ValueError(f"scene '{scene_id}' has no dialogue")
            voices = []
            if voice_params:
                # This scene gets voices of its own, starting from how its
                # speakers sound now, so other scenes are untouched.
                own = audio.scene_voices.setdefault(scene_id, {})
                for cid in sorted({s.character_id for s in targets if s.character_id in base}):
                    own.setdefault(cid, base[cid].model_copy())
                    voices.append(own[cid])
        else:
            targets = lines
            voices = list(base.values()) + [cfg for own in audio.scene_voices.values()
                                            for cfg in own.values()]
        if voice_params:
            for cfg in voices:
                self.audio.apply_voice_params(cfg, voice_params,
                                              characters.get(cfg.character_id))

        affected: List[str] = []
        for seg in targets:
            out = self.audio.render_line(
                state.project_id, seg.scene_id, seg.line_id, seg.text or "",
                audio.voice_for(seg.scene_id, seg.character_id), fallback_ms=seg.duration_ms,
            )
            seg.file_path, seg.duration_ms = out.path, out.duration_ms
            seg.engine, seg.voice = out.engine, out.voice
            affected.append(out.path)
        self._rebuild_audio(state)
        return affected + [state.audio.master_track]

    @staticmethod
    def _script_lines_changed(state: PipelineState) -> bool:
        """Whether the script's lines differ from the ones that were recorded."""
        recorded = {(s.scene_id, s.line_id, s.text) for s in state.audio.manifest.segments
                    if s.kind == "dialogue"}
        written = {(scene.scene_id, ln.line_id, ln.text)
                   for scene in state.script.scenes for ln in scene.dialogue}
        return recorded != written

    def _step_remix_audio(self, state: PipelineState, step: EditStep) -> List[str]:
        """The music, ambience or levels changed: rebuild the beds and the master (no voice is
        recorded again). The film's own picture is not touched; the next step re-joins them."""
        if not state.audio or not state.script:
            return []
        self._rebuild_audio(state, retime=False)
        return [p for p in (state.audio.bgm_track, state.audio.ambience_track,
                            state.audio.master_track) if p]

    def _step_rerecord_line(self, state: PipelineState, step: EditStep) -> List[str]:
        """One line changed: record just that line again, in the voice it has, then lay the film's
        sound out again (the scene is longer or shorter now, and what comes after moves)."""
        audio = state.audio
        if not audio or not state.script:
            return []
        _i, scene = ops.find_scene(state, step.params["scene"])
        _n, line = ops.find_line(scene, step.params["line"])
        seg = next((s for s in audio.manifest.segments if s.kind == "dialogue" and s.line_id == line.line_id), None)
        if seg is None:
            raise ValueError(f"line {line.line_id} was never recorded")
        out = self.audio.render_line(state.project_id, scene.scene_id, line.line_id, line.text,
                                     audio.voice_for(scene.scene_id, seg.character_id),
                                     fallback_ms=line.duration_ms)
        seg.file_path, seg.duration_ms, seg.text = out.path, out.duration_ms, line.text
        seg.engine, seg.voice = out.engine, out.voice
        self._rebuild_audio(state)
        return [out.path, state.audio.master_track]

    def _step_redraw_shot(self, state: PipelineState, step: EditStep) -> List[str]:
        """Draw the picture one shot is cut from again, with a fresh seed. Shots cut from the same
        picture (a medium is a crop of the wide) change together: that is one picture."""
        video = self._require_video(state)
        i, scene = ops.find_scene(state, step.params["scene"])
        frame = next((f for f in video.frames if f.scene_id == scene.scene_id), None)
        shot = frame.shots[int(step.params["shot"]) - 1] if frame and len(frame.shots) >= int(step.params["shot"]) else None
        if shot is None:
            raise ValueError(f"scene {i + 1} has no shot {step.params['shot']}")
        pic_w, pic_h = video_quality.image_size(video.width, video.height)
        salt = f"v{state.version + 1}"
        if shot.image_path in (frame.shot_bank or [frame.image_path]):
            bank = list(frame.shot_bank or [frame.image_path])
            idx = bank.index(shot.image_path)
            bank[idx] = self.video.redraw_picture(state.project_id, scene, idx, pic_w, pic_h,
                                                  seed_salt=salt, story=state.script.story)
            frame.shot_bank = bank
            if idx == 0:
                frame.image_path = bank[0]
                frame.t2v_clip = None          # that clip was made from the old establishing picture
            drawn = bank[idx]
        elif shot.character_id:
            # A close-up is the character's portrait; redraw it for this scene only, as a filter does.
            c = next(c for c in state.script.characters.characters if c.id == shot.character_id)
            drawn = self.video.redraw_portrait_for_scene(state.project_id, c, scene.scene_id, pic_w, pic_h,
                                                         seed_salt=salt, story=state.script.story)
            frame.portrait_overrides[c.id] = drawn
        else:
            raise ValueError("that shot has no picture to draw again")
        return [drawn]

    def _step_restructure(self, state: PipelineState, step: EditStep) -> List[str]:
        """Scenes were removed or moved: the timeline, the sound and the film follow the script's order."""
        affected: List[str] = []
        if state.audio:
            self._rebuild_audio(state)
            affected.append(state.audio.master_track)
        if state.video:
            order = {s.scene_id: n for n, s in enumerate(state.script.scenes)}
            state.video.frames = sorted((f for f in state.video.frames if f.scene_id in order),
                                        key=lambda f: order[f.scene_id])
        return affected

    def _rebuild_audio(self, state: PipelineState, retime: bool = True) -> None:
        if retime:
            self.audio.retime(state)
        self.audio.remix(state)
        self.audio.serialize(state)

    # ---- video frame handlers -------------------------------------------

    def _step_apply_filter(self, state: PipelineState, step: EditStep) -> List[str]:
        """Filter every image a scene shows: its shot bank and its characters' portraits.

        Global scope edits the shared portraits; scene scope writes per-scene
        portrait copies so other scenes are untouched.
        """
        video = self._require_video(state)
        params = step.params or {}
        filt = params.get("filter") or params.get("filter_name") or params.get("aesthetic") \
            or "darker"
        scene_ids = self._scene_ids_in_scope(state, step.scope)
        global_scope = not step.scope.startswith("scene:")
        affected: List[str] = []

        for frame in video.frames:
            if frame.scene_id not in scene_ids:
                continue
            for img in frame.shot_bank or [frame.image_path]:
                affected.append(self._filter_image(img, img, filt))
            if global_scope:
                for char_id, path in frame.portrait_overrides.items():
                    affected.append(self._filter_image(path, path, filt))
            else:
                for char_id in self._speakers(state, frame.scene_id):
                    src = frame.portrait_overrides.get(char_id) or \
                        (video.portrait_for(char_id).image_path if video.portrait_for(char_id) else None)
                    if not src:
                        continue
                    dst = str(project_dir(state.project_id) / "video" / "portraits"
                              / f"{char_id}__{frame.scene_id}.png")
                    affected.append(self._filter_image(src, dst, filt))
                    frame.portrait_overrides[char_id] = dst
        if global_scope:
            for p in video.portraits:
                affected.append(self._filter_image(p.image_path, p.image_path, filt))
        return affected + self._recompose(state)

    def _step_regenerate_scene(self, state: PipelineState, step: EditStep) -> List[str]:
        """New shot-bank images (fresh seed per version) for the scenes in scope."""
        video = self._require_video(state)
        scene_ids = set(self._scene_ids_in_scope(state, step.scope))
        # New pictures are drawn at the picture size, as the first render drew them
        # (the film itself may be bigger; the camera enlarges them).
        pic_w, pic_h = video_quality.image_size(video.width, video.height)
        affected: List[str] = []
        for scene in state.script.scenes:
            if scene.scene_id not in scene_ids:
                continue
            frame = next((f for f in video.frames if f.scene_id == scene.scene_id), None)
            if frame is None:
                continue
            bank = self.video.generate_shot_bank(state.project_id, scene, pic_w,
                                                 pic_h, seed_salt=f"v{state.version + 1}",
                                                 story=state.script.story)
            frame.shot_bank, frame.image_path = bank, bank[0]
            frame.t2v_clip = None  # was generated from the old establishing image
            affected.extend(bank)
        if not affected:
            raise ValueError(f"no scene matches scope '{step.scope}'")
        return affected + self._recompose(state)

    def _step_regenerate_portraits(self, state: PipelineState, step: EditStep) -> List[str]:
        """Change character design: regenerate portraits (all, or the one in scope)."""
        video = self._require_video(state)
        if step.scope.startswith("character:"):
            char_ids = {step.scope.split(":", 1)[1]}
        else:
            char_ids = {c.id for c in state.script.characters.characters if c.role != "narrator"}
        affected: List[str] = []
        salt = f"v{state.version + 1}"
        pic_w, pic_h = video_quality.image_size(video.width, video.height)
        for c in state.script.characters.characters:
            if c.id not in char_ids:
                continue
            # Re-roll the locked look and keep it, so later re-renders don't
            # quietly restore the old design.
            c.appearance_lock = build_appearance_lock(c, salt)
            c.image_seed = character_seed(c, salt)
            new = self.video.generate_portrait(state.project_id, c, pic_w, pic_h,
                                               story=state.script.story)
            video.portraits = [new if p.character_id == c.id else p for p in video.portraits]
            if not video.portrait_for(c.id):
                video.portraits.append(new)
            for frame in video.frames:
                frame.portrait_overrides.pop(c.id, None)
            affected.append(new.image_path)
        if affected:
            from agents.story_agent import StoryAgent
            StoryAgent().serialize(state)      # characters.json records the new look
        if not affected:
            raise ValueError(f"no character matches scope '{step.scope}'")
        return affected + self._recompose(state)

    # ---- video handlers --------------------------------------------------

    def _step_recompose_video(self, state: PipelineState, step: EditStep) -> List[str]:
        video = self._require_video(state)
        if "subtitles" in (step.params or {}):
            video.has_subtitles = bool(step.params["subtitles"])
        return self._recompose(state)

    def _step_change_speed(self, state: PipelineState, step: EditStep) -> List[str]:
        video = self._require_video(state)
        factor = float((step.params or {}).get("factor", 1.5))
        if factor <= 0:
            raise ValueError(f"invalid speed factor {factor}")
        video.speed_factor = max(MIN_SPEED, min(MAX_SPEED, round(video.speed_factor * factor, 4)))
        return self._recompose(state)

    # ---- script handlers -------------------------------------------------

    def _step_regenerate_script(self, state: PipelineState, step: EditStep) -> List[str]:
        from agents.story_agent import StoryAgent
        params = step.params or {}
        if params.get("genre"):
            state.user_prompt = f"{state.user_prompt} (genre: {params['genre']})"
        story = state.script.story
        # A rewritten script is still in the format and style the creator chose.
        titles = story.graphics
        StoryAgent().run(state, target_duration_s=story.target_duration_s,
                         scene_count=len(state.script.scenes), format=story.format,
                         style=story.style or "realistic")
        state.script.story.graphics = titles          # a rewritten story keeps the titles the creator chose
        return list(state.phase1.artifact_paths)

    def _step_rerun_video(self, state: PipelineState, step: EditStep) -> List[str]:
        v = state.video
        self.video.run(
            state,
            with_subtitles=v.has_subtitles if v else True,
            subtitle_language=v.subtitle_language if v else "English",
            width=v.width if v else film_format.film_size(state.script.story.format)[0],
            height=v.height if v else film_format.film_size(state.script.story.format)[1],
            fps=v.fps if v else 24,
            use_text_to_video=v.used_text_to_video if v else None,
            use_lip_sync=v.used_lip_sync if v else None,
            cinematic_post=v.cinematic_post if v else True,
        )
        return list(state.phase3.artifact_paths)

    # ---- helpers ---------------------------------------------------------

    def _recompose(self, state: PipelineState) -> List[str]:
        self.video.compose(state)
        return self.video.serialize(state)

    def _filter_image(self, src: str, dst: str, filt: str) -> str:
        res = self.tools.execute("vision.edit_image", in_path=src, out_path=dst, filters=[filt])
        if not res.success:
            raise ValueError(res.error)
        return res.data

    @staticmethod
    def _require_video(state: PipelineState):
        if not state.video or not state.script:
            raise ValueError("this edit needs a rendered video; run the pipeline first")
        return state.video

    @staticmethod
    def _speakers(state: PipelineState, scene_id: str) -> List[str]:
        scene = next((s for s in state.script.scenes if s.scene_id == scene_id), None)
        return sorted({ln.character_id for ln in scene.dialogue}) if scene else []

    @staticmethod
    def _scene_ids_in_scope(state: PipelineState, scope: str) -> List[str]:
        all_ids = [s.scene_id for s in state.script.scenes] if state.script else []
        if scope.startswith("scene:"):
            sid = scope.split(":", 1)[1]
            if sid not in all_ids:
                raise ValueError(f"unknown scene '{sid}' (have: {', '.join(all_ids)})")
            return [sid]
        return all_ids
