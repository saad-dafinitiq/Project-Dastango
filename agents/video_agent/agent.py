"""Phase 3 agent: ScriptOutput + AudioOutput -> final MP4 (cinematic, in sync).

Two stages:

1. **Assets** (`run`): one portrait per character and a shot bank per scene
   (the wide picture, plus a detail cutaway for a long scene), plus an optional
   real-motion clip for the establishing shot when a text/image-to-video
   provider is configured.

2. **Composition** (`compose`, also used by edits): every scene is cut to the
   audio timeline from Phase 2 (shared/timeline.py). The shots are planned by
   `coverage.plan_coverage` (DST-36): 2 to 4 per scene, a wide shot that opens
   it, a cut to whoever is speaking, a pull-out that closes it, each cropped or
   framed from the scene's picture or a character's portrait so extra shots do
   not cost extra images. A scene with a real lip-sync or text-to-video clip is
   planned the earlier way (`_plan_scene_classic`), which those clips need.
   Shot boundaries are the timeline's boundaries rounded to frames, and every
   clip is rendered with extra frames to cover its crossfade, so the final
   video is exactly as long as the master audio and every cut lands on its
   line. Scenes whose shot plan is unchanged are reused, not re-rendered.
   Then scenes are crossfaded, the master audio is muxed, an optional speed
   change is applied, and subtitle tracks are embedded.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import time
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agents.story_agent.appearance import build_appearance_lock, character_seed
from mcp.tool_executor import ToolExecutor
from shared import film_format, progress_hook, providers, stand_in, transitions, video_quality
from shared.constants import DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH, PHASE_VIDEO
from shared.languages import canonical
from shared.schemas.audio import AudioSegment, SceneTiming
from shared.schemas.pipeline import PipelineState
from agents.story_agent.visual_style import portrait_style, scene_style
from shared.schemas.story import Character, Scene, StoryOutput
from shared.schemas.storyboard import (
    Storyboard, StoryboardFrame, StoryboardLine, StoryboardPortrait,
)
from shared.schemas.video import CharacterPortrait, SceneFrame, Shot, VideoOutput
from shared.timeline import (
    SCENE_XFADE_MS, SHOT_XFADE_MS, build_timeline, span_frames, split_span,
    xfade_frames,
)
from shared.utils.files import project_dir, write_json
from shared.utils.parallel import run_jobs
from shared.utils.logging import get_logger

from . import coverage
from .animator import (
    Shot as RenderShot, assemble_scene, pick_motion_for_index, render_shot, shot_workers,
)


def premium_motion_available() -> bool:
    """True when a real image-to-video provider is configured and usable."""
    spec = providers.active("video")
    return bool(spec and spec.provider != "ffmpeg")


def premium_lipsync_available() -> bool:
    spec = providers.active("lipsync")
    return bool(spec and spec.provider != "none")


def motion_provider() -> str:
    spec = providers.active("video")
    return spec.provider if spec else "ffmpeg"

log = get_logger("video_agent")

MAX_SHOT_MS = 4500   # longest a single still stays on screen before a cut
MIN_SHOT_MS = 1500   # shortest cut we'll create when splitting a long span

# The pictures drawn for a scene. The wide one is also the storyboard preview, and a
# medium shot is a crop of it. A scene long enough (config coverage.cutaway_scene_s)
# also gets a detail picture to cut away to; shorter ones cost a single image.
BANK_FRAMINGS = [
    ("wide",   "wide establishing shot, full landscape, expansive composition"),
    ("detail", "extreme close-up detail, macro, intricate texture, shallow depth of field"),
]


def framings_for(scene: Scene) -> List[Tuple[str, str]]:
    """The pictures this scene needs: the wide shot, and a cutaway if it is long."""
    return BANK_FRAMINGS if coverage.needs_cutaway(scene.duration_ms) else BANK_FRAMINGS[:1]


@dataclass
class PlannedShot:
    shot_id: str
    kind: str                     # establishing | character | lip_sync
    source: str                   # image path, or video path when is_video
    start_ms: int
    end_ms: int
    nominal_frames: int
    render_frames: int
    motion: str
    character_id: Optional[str] = None
    audio_path: Optional[str] = None
    is_video: bool = False
    framing: str = ""             # wide | medium | close | cutaway (DST-36)
    crop: Optional[Tuple[float, float, float]] = None   # (width fraction, x, y)
    # How this shot goes out into the next one (DST-39; set by `_apply_transitions`): the style,
    # the frames of overlap (already inside `render_frames`), and whether the creator chose it.
    out_kind: str = "cut"
    out_frames: int = 0
    custom_out: bool = False


class VideoAgent:
    def __init__(self):
        self.tools = ToolExecutor()

    # ---- public ----------------------------------------------------------

    def run(self, state: PipelineState, with_subtitles: bool = True,
            subtitle_language: str = "English",
            width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
            fps: int = DEFAULT_FPS,
            use_text_to_video: Optional[bool] = None,
            use_lip_sync: Optional[bool] = None,
            cinematic_post: bool = True,
            burn_subtitles: bool = True) -> VideoOutput:
        if not state.script:
            raise ValueError("phase 3 requires state.script (run phase 1 first)")
        log.info("phase 3 start (project=%s, %dx%d@%d, subs=%s, lang=%s)",
                 state.project_id, width, height, fps, with_subtitles, subtitle_language)
        state.phase3.status = "running"
        state.phase3.started_at = datetime.utcnow().isoformat()

        # Which tier serves this run comes from config/providers.yaml, like
        # every other model choice. An `if os.getenv(...)` chain in here was
        # exactly what the provider layer exists to replace.
        if use_text_to_video is None:
            use_text_to_video = premium_motion_available()
        if use_lip_sync is None:
            use_lip_sync = premium_lipsync_available()

        # The film is `width` x `height`, but the pictures are drawn at the picture
        # size: the camera enlarges them itself, so storing them at film size would
        # only cost disk (config/video.yaml, images). Previews drawn at that size
        # are still the ones a render reuses.
        pic_w, pic_h = video_quality.image_size(width, height)

        try:
            # Portraits and every scene's shot bank are independent: generate them
            # as concurrently as the active image provider allows.
            portraits = run_jobs(
                [(self.generate_portrait, (state.project_id, c, pic_w, pic_h, "",
                                           state.script.story,
                                           self.reusable_portrait(state, c, pic_w, pic_h)))
                 for c in state.script.characters.characters],
                workers=providers.concurrency("image"), label="portraits", step="portraits",
            )
            frames = run_jobs(
                [(self._generate_scene_frame,
                  (state.project_id, scene, pic_w, pic_h, fps, use_text_to_video,
                   state.script.story,
                   self.reusable_preview(state, scene, pic_w, pic_h)))
                 for scene in state.script.scenes],
                workers=providers.concurrency("image"), label="scene images", step="pictures",
            )

            lang = canonical(subtitle_language)
            if subtitle_language and not lang:
                log.warning("subtitle language %r is not supported — using English",
                            subtitle_language)
            state.video = VideoOutput(
                project_id=state.project_id,
                legacy_transitions=False,
                frames=frames,
                final_video_path="",
                width=width, height=height, fps=fps,
                has_subtitles=with_subtitles,
                portraits=portraits,
                used_text_to_video=use_text_to_video,
                used_lip_sync=use_lip_sync,
                cinematic_post=cinematic_post,
                subtitle_language=lang or "English",
                burn_subtitles=burn_subtitles,
            )
            final_video = self.compose(state)

            state.phase3.status = "complete"
            state.phase3.finished_at = datetime.utcnow().isoformat()
            state.phase3.artifact_paths = self.serialize(state)
            log.info("phase 3 complete (%d scenes, %d shots total, video=%s)",
                     len(frames), sum(len(f.shots) for f in state.video.frames), final_video)
            return state.video
        except Exception as e:  # noqa: BLE001
            state.phase3.status = "failed"
            state.phase3.error = f"{type(e).__name__}: {e}"
            log.exception("phase 3 failed")
            raise

    def compose(self, state: PipelineState, force: bool = False) -> str:
        """Cut every scene to the current timeline and build the final film.

        Reuses a scene's existing clip when its shot plan and source images are
        unchanged (unless `force`). Returns the final video path.
        """
        video = state.video
        timings = self._scene_timings(state)
        scene_by_id = {s.scene_id: s for s in state.script.scenes}
        segs_by_scene: Dict[str, List[AudioSegment]] = {}
        if state.audio:
            for seg in state.audio.manifest.segments:
                if seg.kind == "dialogue":
                    segs_by_scene.setdefault(seg.scene_id, []).append(seg)

        # Plan every scene before rendering any, so the shots of all the scenes
        # that changed render together rather than one scene at a time.
        planned_scenes = []
        last_index = len(video.frames) - 1
        for i, frame in enumerate(video.frames):
            planned_scenes.append((frame, self._plan_scene(
                state, scene_by_id[frame.scene_id], frame, timings[frame.scene_id],
                segs_by_scene.get(frame.scene_id, []), is_last_scene=(i == last_index))))
        self._apply_transitions(state, planned_scenes)
        plans = []
        for frame, planned in planned_scenes:
            signature = self._signature(planned, video, scene_by_id[frame.scene_id].effect)
            clip_ok = frame.clip_path and Path(frame.clip_path).exists()
            stale = force or not clip_ok or frame.plan_signature != signature
            plans.append((frame, planned, signature, stale))

        clips = self._render_scenes(state, [(f.scene_id, planned)
                                            for f, planned, _, stale in plans if stale])
        for frame, planned, signature, stale in plans:
            if stale:
                frame.clip_path = str(clips[frame.scene_id])
                frame.plan_signature = signature
            frame.shots = [self._to_shot(frame.scene_id, p) for p in planned]
            frame.start_ms = timings[frame.scene_id].start_ms
            frame.duration_ms = timings[frame.scene_id].duration_ms
        log.info("composition: %d scene(s) rendered, %d reused",
                 len(clips), len(plans) - len(clips))

        progress_hook.report("cut", 0, 1)
        final = self._compose_final(state)
        progress_hook.report("cut", 1, 1)
        video.final_video_path = str(final)
        video.duration_ms = int(round(sum(t.duration_ms for t in timings.values())
                                      / video.speed_factor))
        return str(final)

    def serialize(self, state: PipelineState) -> List[str]:
        """Write video_summary.json; returns every Phase 3 artifact path."""
        output = state.video
        proj = project_dir(state.project_id)
        summary = proj / "video_summary.json"
        write_json(summary, {
            "project_id": state.project_id,
            "phase": PHASE_VIDEO,
            "status": "complete",
            "final_video": output.final_video_path,
            "frame_count": len(output.frames),
            "shot_count": sum(len(f.shots) for f in output.frames),
            "has_subtitles": output.has_subtitles,
            "subtitle_languages": output.subtitle_languages,
            "used_text_to_video": output.used_text_to_video,
            "used_lip_sync": output.used_lip_sync,
            "speed_factor": output.speed_factor,
            "width": output.width, "height": output.height, "fps": output.fps,
            "duration_ms": output.duration_ms,
            "frames": [f.model_dump(mode="json") for f in output.frames],
            "portraits": [p.model_dump(mode="json") for p in output.portraits],
        })
        artifacts: List[str] = [str(summary), output.final_video_path]
        for f in output.frames:
            artifacts.append(f.image_path)
            artifacts.extend(f.shot_bank)
            artifacts.extend(f.portrait_overrides.values())
            for extra in (f.clip_path, f.t2v_clip):
                if extra:
                    artifacts.append(extra)
            artifacts.extend(s.clip_path for s in f.shots if s.clip_path)
        for p in output.portraits:
            artifacts.append(p.image_path)
            if p.talking_head_clip:
                artifacts.append(p.talking_head_clip)
        seen = set()
        state.phase3.artifact_paths = [a for a in artifacts
                                       if a and not (a in seen or seen.add(a))]
        return state.phase3.artifact_paths

    # ---- assets ----------------------------------------------------------

    @staticmethod
    def _portrait_request(c: Character, story: Optional[StoryOutput],
                          seed_salt: str = "") -> Tuple[str, str, str, int]:
        """(prompt, style, negative prompt, seed) of a character's portrait: one
        definition for the cast row's portrait and the render's, so they match."""
        # The appearance lock (not the raw description) keeps the face stable.
        look = c.appearance_lock or build_appearance_lock(c)
        style, negative = portrait_style(story)
        prompt = f"close-up portrait of {look}, {c.role}"
        seed = character_seed(c, seed_salt) if seed_salt else (
            c.image_seed if c.image_seed is not None else character_seed(c))
        return prompt, style, negative, seed

    @classmethod
    def portrait_signature(cls, c: Character, story: Optional[StoryOutput],
                           width: int, height: int) -> str:
        """Identifies the image a portrait holds, as `preview_signature` does for
        a scene: two matching signatures mean two identical images."""
        prompt, style, negative, seed = cls._portrait_request(c, story)
        return hashlib.md5(
            f"{prompt}|{style}|{negative}|{seed}|{width}x{height}".encode()).hexdigest()

    def redraw_picture(self, project_id: str, scene: Scene, index: int, width: int, height: int,
                       seed_salt: str, story: Optional[StoryOutput] = None) -> str:
        """Draw one of a scene's pictures again (0 is the wide, 1 the cutaway of a long scene)."""
        tag, suffix = framings_for(scene)[index]
        out = project_dir(project_id) / "video" / "frames" / f"{scene.scene_id}_{tag}.png"
        style, negative = scene_style(story, scene.tone)
        prompt = f"{scene.visual_prompt}, {suffix}"
        res = self.tools.execute("vision.generate_image", prompt=prompt, out_path=str(out), width=width,
                                 height=height, style=style, negative_prompt=negative,
                                 seed=_seed(prompt, seed_salt))
        path = _ensure_image(res, out, prompt, width, height)
        meta = res.metadata or {}
        stand_in.mark(out, not res.success or (meta.get("provider") == "placeholder" and bool(meta.get("fallback"))))
        return path

    def redraw_portrait_for_scene(self, project_id: str, c: Character, scene_id: str, width: int,
                                  height: int, seed_salt: str, story: Optional[StoryOutput] = None) -> str:
        """A character's portrait drawn again for one scene (the other scenes keep theirs)."""
        out = project_dir(project_id) / "video" / "portraits" / f"{c.id}__{scene_id}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        prompt, style, negative, seed = self._portrait_request(c, story, seed_salt)
        res = self.tools.execute("vision.generate_image", prompt=prompt, out_path=str(out), width=width,
                                 height=height, style=style, negative_prompt=negative, seed=seed)
        return _ensure_image(res, out, prompt, width, height)

    def generate_portrait(self, project_id: str, c: Character, width: int, height: int,
                          seed_salt: str = "", story: Optional[StoryOutput] = None,
                          reusable: Optional[str] = None) -> CharacterPortrait:
        """(Re)generate one character's close-up portrait, in the film's style.

        `reusable` is the portrait the storyboard already drew from the same look,
        seed, style and size (`reusable_portrait`): it is the very image this call
        would make, so it is kept instead of paying for it twice.
        """
        out = project_dir(project_id) / "video" / "portraits" / f"{c.id}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        if reusable and not seed_salt and Path(reusable).exists():
            if Path(reusable).resolve() != out.resolve():
                shutil.copyfile(reusable, out)
            log.info("  portrait: %s -> %s (reused the storyboard portrait)", c.name, out.name)
            return CharacterPortrait(character_id=c.id, image_path=str(out))
        prompt, style, negative, seed = self._portrait_request(c, story, seed_salt)
        res = self.tools.execute(
            "vision.generate_image", prompt=prompt, out_path=str(out),
            width=width, height=height, style=style,
            negative_prompt=negative, seed=seed,
        )
        log.info("  portrait: %s -> %s (%s)", c.name, out.name,
                 res.metadata.get("provider") if res.success else res.error)
        return CharacterPortrait(character_id=c.id,
                                 image_path=_ensure_image(res, out, prompt, width, height))

    def generate_storyboard(self, state: PipelineState, width: Optional[int] = None,
                            height: Optional[int] = None,
                            scene_ids: Optional[List[str]] = None,
                            on_frame: Optional[Any] = None,
                            failed: Optional[List[str]] = None,
                            character_ids: Optional[List[str]] = None,
                            on_portrait: Optional[Any] = None) -> Storyboard:
        """One small preview image per scene and one portrait per character, for
        approval before the real render.

        A preview costs a single image; a full render costs three per scene plus
        every voice line and all the video work. The portraits are drawn here, for
        the cast row, and the render keeps them (`reusable_portrait`), so they add
        nothing to the bill.

        A scene whose picture could not be drawn (every provider failed, or only
        the placeholder answered after the real ones failed) keeps the picture it
        had, and its id is added to `failed` if a list is passed. It is never
        stamped as matching its text: the render must redraw it, not reuse a
        stale or placeholder image under a signature that says it is current.
        The same holds for a portrait. `character_ids` picks which portraits to
        draw as `scene_ids` does for scenes (None: all, []: none); `on_portrait` is
        called with (character, path, drawn) as each one finishes.
        """
        script = state.script
        if width is None or height is None:
            # Previews are pictures, drawn at the picture size, not at film size.
            width, height = video_quality.image_size(*film_format.film_size(script.story.format))
        board = state.storyboard or Storyboard(
            project_id=state.project_id, prompt=state.user_prompt,
            title=script.story.title, logline=script.story.logline,
            preview_width=width, preview_height=height,
        )
        names = {c.id: c.name for c in script.characters.characters}
        # scene_ids=None redraws every preview; an empty list redraws none
        # (a text-only edit shouldn't spend images).
        targets = [s for s in script.scenes
                   if scene_ids is None or s.scene_id in scene_ids]
        drawn: Dict[str, bool] = {}

        def draw(scene: Scene) -> str:
            path, ok = self._draw_preview(state.project_id, scene, width, height,
                                          script.story)
            drawn[scene.scene_id] = ok
            if not ok and failed is not None:
                failed.append(scene.scene_id)
            # Each frame is reported the moment it exists, so the interface can
            # fill the storyboard in scene by scene rather than all at the end.
            if on_frame is not None:
                on_frame(scene, path)
            return path

        cast_targets = [c for c in script.characters.characters
                        if character_ids is None or c.id in character_ids]
        portrait_drawn: Dict[str, bool] = {}

        def draw_portrait(c: Character) -> str:
            path, ok = self._draw_portrait(state.project_id, c, width, height, script.story)
            portrait_drawn[c.id] = ok
            if on_portrait is not None:
                on_portrait(c, path, ok)
            return path

        # One batch, cast first: the cast row sits above the scenes, so it should
        # fill in first, and the portraits share the image provider's limits.
        results = run_jobs(
            [(draw_portrait, (c,)) for c in cast_targets]
            + [(draw, (scene,)) for scene in targets],
            workers=providers.concurrency("image"), label="storyboard previews and portraits",
        )
        portrait_by_char = dict(zip([c.id for c in cast_targets], results[:len(cast_targets)]))
        preview_by_scene = dict(zip([s.scene_id for s in targets], results[len(cast_targets):]))

        frames = []
        for scene in script.scenes:
            existing = board.frame(scene.scene_id)
            frames.append(StoryboardFrame(
                scene_id=scene.scene_id, index=scene.index, title=scene.title,
                setting=scene.setting, visual_prompt=scene.visual_prompt,
                preview_path=preview_by_scene.get(
                    scene.scene_id, existing.preview_path if existing else None),
                preview_signature=(
                    self.preview_signature(scene, script.story, width, height)
                    if drawn.get(scene.scene_id)
                    else (existing.preview_signature if existing else None)),
                dialogue=[StoryboardLine(line_id=ln.line_id, character_id=ln.character_id,
                                         character_name=names.get(ln.character_id, ""),
                                         text=ln.text)
                          for ln in scene.dialogue],
                estimated_ms=scene.duration_ms,
            ))
        board.frames = frames
        portraits = []
        for c in script.characters.characters:
            existing = board.portrait(c.id)
            if c.id in portrait_by_char:
                real = portrait_drawn.get(c.id, False)
                portraits.append(StoryboardPortrait(
                    character_id=c.id, path=portrait_by_char[c.id],
                    signature=(self.portrait_signature(c, script.story, width, height)
                               if real else (existing.signature if existing else None))))
            elif existing:
                portraits.append(existing)
        board.portraits = portraits
        board.title, board.logline = script.story.title, script.story.logline
        board.touch()
        state.storyboard = board
        state.stage = "storyboard" if state.stage == "draft" else state.stage
        return board

    @staticmethod
    def preview_signature(scene: Scene, story: Optional[StoryOutput],
                          width: int, height: int) -> str:
        """Identifies the image a preview holds.

        The wide shot is generated from exactly this prompt, style and seed, so
        two signatures matching means the two images would be identical.
        """
        style, negative = scene_style(story, scene.tone)
        prompt = f"{scene.visual_prompt}, {BANK_FRAMINGS[0][1]}"
        return hashlib.md5(
            f"{prompt}|{style}|{negative}|{width}x{height}".encode()).hexdigest()

    def _draw_preview(self, project_id: str, scene: Scene, width: int, height: int,
                      story: StoryOutput) -> Tuple[str, bool]:
        """Draw one scene's preview. Returns (path, drawn).

        `drawn` is False when no real image came back: every provider failed, or
        only the offline placeholder answered after the real ones had failed
        (quota used up). The picture is drawn beside the old one and moved over it
        only when it is real, so a failed redraw leaves the previous picture
        exactly as it was; before this, the offline fallback overwrote a good
        preview with a gradient (DST-25). If there never was a picture, a
        placeholder is written so the card has something to show, and `drawn` is
        still False so the render draws the scene properly.
        """
        out = project_dir(project_id) / "video" / "storyboard" / f"{scene.scene_id}.png"
        style, negative = scene_style(story, scene.tone)
        prompt = f"{scene.visual_prompt}, {BANK_FRAMINGS[0][1]}"
        # A new prompt gives a new preview; the same prompt reuses the look.
        return self._draw_in_place(out, f"storyboard {scene.scene_id}", prompt, style,
                                   negative, _seed(prompt, ""), width, height)

    def _draw_portrait(self, project_id: str, c: Character, width: int, height: int,
                       story: StoryOutput) -> Tuple[str, bool]:
        """Draw one character's portrait where the render keeps it, by the same
        rule as a preview: `drawn` is False when no real image came back, the
        previous portrait is left as it was, and a placeholder only stands in
        when there never was one."""
        out = project_dir(project_id) / "video" / "portraits" / f"{c.id}.png"
        prompt, style, negative, seed = self._portrait_request(c, story)
        return self._draw_in_place(out, f"portrait {c.name}", prompt, style, negative,
                                   seed, width, height)

    def _draw_in_place(self, out: Path, label: str, prompt: str, style: str, negative: str,
                       seed: int, width: int, height: int) -> Tuple[str, bool]:
        """Draw `out` beside itself and move the new image over it only if it is
        real. Returns (path, drawn)."""
        out.parent.mkdir(parents=True, exist_ok=True)
        staging = out.with_name(f"{out.stem}.new{out.suffix}")
        res = self.tools.execute(
            "vision.generate_image", prompt=prompt, out_path=str(staging),
            width=width, height=height, style=style,
            negative_prompt=negative, seed=seed,
        )
        meta = res.metadata or {}
        real = bool(res.success and res.data and Path(res.data).exists()
                    and not (meta.get("provider") == "placeholder" and meta.get("fallback")))
        log.info("  %s -> %s (%s)", label, out.name,
                 meta.get("provider") if res.success else res.error)
        if real:
            _replace_file(Path(res.data), out)
            return str(out), True
        for leftover in {staging, Path(res.data)} if res.data else {staging}:
            leftover.unlink(missing_ok=True)
        log.warning("  %s: no real image was produced, keeping %s", label,
                    "the previous picture" if out.exists() else "a placeholder")
        if not out.exists():
            from mcp.tools.vision_tools.image_gen_tool import write_placeholder
            write_placeholder(prompt, out, width, height)
        return str(out), False

    def generate_shot_bank(self, project_id: str, scene: Scene, width: int, height: int,
                           seed_salt: str = "", story: Optional[StoryOutput] = None,
                           reusable_preview: Optional[str] = None) -> List[str]:
        """(Re)generate a scene's pictures (the wide one, and a detail for a long
        scene), in the film's style.

        `reusable_preview` is the storyboard preview for this scene when it was
        drawn from the same prompt at the same size — the wide shot would be
        that identical image, so it is copied rather than generated again.
        """
        img_dir = project_dir(project_id) / "video" / "frames"
        img_dir.mkdir(parents=True, exist_ok=True)
        style, negative = scene_style(story, scene.tone)
        bank: List[str] = []
        for tag, suffix in framings_for(scene):
            out = img_dir / f"{scene.scene_id}_{tag}.png"
            prompt = f"{scene.visual_prompt}, {suffix}"
            if (tag == "wide" and not seed_salt and reusable_preview
                    and Path(reusable_preview).exists()):
                shutil.copyfile(reusable_preview, out)
                bank.append(str(out))
                stand_in.mark(out, False)
                log.info("  scene %s wide shot -> %s (reused the storyboard preview)",
                         scene.scene_id, out.name)
                continue
            res = self.tools.execute(
                "vision.generate_image", prompt=prompt, out_path=str(out),
                width=width, height=height, style=style,
                negative_prompt=negative, seed=_seed(prompt, seed_salt),
            )
            bank.append(_ensure_image(res, out, prompt, width, height))
            # Told to the creator when the film is done: a placeholder in place of a picture.
            # (A placeholder chosen on purpose, as offline runs do, is not a stand-in:
            # it is the placeholder answering after the real providers failed that is.)
            meta = res.metadata or {}
            stand_in.mark(out, not res.success or (meta.get("provider") == "placeholder"
                                                   and bool(meta.get("fallback"))))
            log.info("  scene %s %s shot -> %s (%s)", scene.scene_id, tag, out.name,
                     res.metadata.get("provider") if res.success else res.error)
        return bank

    def reusable_preview(self, state: PipelineState, scene: Scene,
                         width: int, height: int) -> Optional[str]:
        """The storyboard preview for this scene, if it is the same image the
        wide shot would be.

        It stops matching the moment the scene's visuals, the film's style or
        the render size change, which is exactly when the shot has to be drawn
        again anyway.
        """
        board = state.storyboard
        frame = board.frame(scene.scene_id) if board else None
        if not frame or not frame.preview_path or not frame.preview_signature:
            return None
        story = state.script.story if state.script else None
        if frame.preview_signature != self.preview_signature(scene, story, width, height):
            return None
        return frame.preview_path if Path(frame.preview_path).exists() else None

    def reusable_portrait(self, state: PipelineState, c: Character,
                          width: int, height: int) -> Optional[str]:
        """The storyboard's portrait of this character, if the render would draw
        the very same image: same appearance lock, seed, style and size. It stops
        matching when any of them changes, which is when it must be drawn again."""
        board = state.storyboard
        stored = board.portrait(c.id) if board else None
        if not stored or not stored.path or not stored.signature:
            return None
        story = state.script.story if state.script else None
        if stored.signature != self.portrait_signature(c, story, width, height):
            return None
        return stored.path if Path(stored.path).exists() else None

    def _generate_scene_frame(self, project_id: str, scene: Scene, width: int, height: int,
                              fps: int, use_text_to_video: bool,
                              story: Optional[StoryOutput] = None,
                              reusable_preview: Optional[str] = None) -> SceneFrame:
        bank = self.generate_shot_bank(project_id, scene, width, height, story=story,
                                       reusable_preview=reusable_preview)
        t2v_clip = None
        if use_text_to_video:
            t2v_out = project_dir(project_id) / "video" / "t2v" / f"{scene.scene_id}.mp4"
            t2v_out.parent.mkdir(parents=True, exist_ok=True)
            # Image-to-video — the wide still gives the model a strong visual anchor.
            res = self.tools.execute(
                "vision.text_to_video", prompt=scene.visual_prompt, image_path=bank[0],
                out_path=str(t2v_out), duration_s=4.0, width=width, height=height, fps=fps,
            )
            if res.success:
                t2v_clip = res.data
                log.info("  scene %s: t2v clip generated (%s)", scene.scene_id,
                         res.metadata.get("provider"))
            else:
                log.info("  scene %s: t2v unavailable (%s) — using still",
                         scene.scene_id, res.error)
        return SceneFrame(scene_id=scene.scene_id, image_path=bank[0], shot_bank=bank,
                          width=width, height=height, duration_ms=scene.duration_ms,
                          transition_in=scene.transition_in, t2v_clip=t2v_clip)

    # ---- shot planning ---------------------------------------------------

    def _scene_timings(self, state: PipelineState) -> Dict[str, SceneTiming]:
        if state.audio and state.audio.manifest.scenes:
            return {t.scene_id: t for t in state.audio.manifest.scenes}
        # No audio timeline (e.g. video-only runs): scenes play their script length.
        slots, _, _ = build_timeline([(s.scene_id, [], s.duration_ms)
                                      for s in state.script.scenes])
        return {s.scene_id: SceneTiming(scene_id=s.scene_id, start_ms=s.start_ms,
                                        end_ms=s.end_ms) for s in slots}

    def _plan_scene(self, state: PipelineState, scene: Scene, frame: SceneFrame,
                    timing: SceneTiming, segments: List[AudioSegment],
                    is_last_scene: bool) -> List[PlannedShot]:
        """Decide every cut in a scene. Pure planning — nothing is rendered.

        A scene is covered (DST-36): a wide shot, a cut to whoever speaks, a pull-out
        at the end. A scene that has a real lip-sync or text-to-video clip is planned
        the earlier way, since those clips are the picture for their span.
        """
        video = state.video
        if video.used_lip_sync or (frame.t2v_clip and Path(frame.t2v_clip).exists()):
            return self._plan_scene_classic(state, scene, frame, timing, segments,
                                            is_last_scene)
        bank = frame.shot_bank or [frame.image_path]
        people = {c.id: c for c in state.script.characters.characters}

        def portrait_of(character_id: str) -> Optional[str]:
            path = frame.portrait_overrides.get(character_id)
            if not path:
                p = video.portrait_for(character_id)
                path = p.image_path if p else None
            return path if path and Path(path).exists() else None

        lines = [coverage.CoverLine(
                    seg.character_id or "",
                    narrator=(people.get(seg.character_id) is None
                              or people[seg.character_id].role == "narrator"),
                    start_ms=seg.start_ms, end_ms=seg.start_ms + seg.duration_ms,
                    text=seg.text or "")
                 for seg in segments]
        covers = coverage.plan_coverage(
            timing.start_ms, timing.end_ms, lines, getattr(scene, "tone", "") or "",
            nominal_ms=scene.duration_ms, scene_index=scene.index,
            has_portrait=lambda cid: portrait_of(cid) is not None,
            has_cutaway=len(bank) > 1, scene_id=scene.scene_id,
            overrides=scene.shot_overrides, tall=video.height > video.width)

        planned: List[PlannedShot] = []
        for j, c in enumerate(covers):
            if c.source == "portrait":
                source = portrait_of(c.character_id) or bank[0]
            elif c.source == "cutaway":
                source = bank[1] if len(bank) > 1 else bank[0]
            else:
                source = bank[0]
            kind = {"wide": "establishing", "close": "character", "cutaway": "broll"}.get(
                c.framing, "character" if c.speakers else "establishing")
            planned.append(PlannedShot(
                shot_id=coverage.shot_id_at(frame.scene_id, j),
                kind=kind, source=source, start_ms=c.start_ms, end_ms=c.end_ms,
                nominal_frames=span_frames(c.start_ms, c.end_ms, video.fps),
                render_frames=span_frames(c.start_ms, c.end_ms, video.fps),
                motion=c.move, character_id=c.character_id, framing=c.framing, crop=c.crop))
        return planned

    def _apply_transitions(self, state: PipelineState,
                           plans: List[Tuple[SceneFrame, List[PlannedShot]]]) -> None:
        """Decide how every shot gives way to the next, across the whole film (DST-39).

        A transition starts on the cut and runs past it, so the shot before the cut gets
        the overlap as extra rendered frames and the film stays exactly the timeline's
        length (shared/transitions.py). Inside a scene the creator's choice is on the shot
        that comes in; between scenes it is on the scene (`Scene.cut_in`). Without one, the
        default: cuts inside a scene and a soft fade between scenes, or, for a film made
        before this, the 200 ms and 400 ms fades it was cut with.
        """
        video = state.video
        scene_by_id = {s.scene_id: s for s in state.script.scenes}
        flat = [(si, j, p) for si, (_f, planned) in enumerate(plans) for j, p in enumerate(planned)]
        for _si, _j, p in flat:
            p.out_kind, p.out_frames, p.custom_out = "cut", 0, False
            p.render_frames = p.nominal_frames
        for (si, _j, a), (sj, jj, b) in zip(flat, flat[1:]):
            scene = scene_by_id[plans[sj][0].scene_id]
            spec = coverage.transition_into(scene, jj)
            kind, ms = transitions.resolve(spec, between_scenes=sj != si, legacy=video.legacy_transitions)
            n = transitions.overlap_frames(kind, ms, video.fps, a.nominal_frames, b.nominal_frames)
            a.out_kind, a.out_frames, a.custom_out = (kind if n else "cut"), n, spec is not None
            a.render_frames = a.nominal_frames + n

    def _plan_scene_classic(self, state: PipelineState, scene: Scene, frame: SceneFrame,
                            timing: SceneTiming, segments: List[AudioSegment],
                            is_last_scene: bool) -> List[PlannedShot]:
        """The earlier plan: a cut per line, split every few seconds, from a bank."""
        video = state.video
        fps = video.fps
        bank = frame.shot_bank or [frame.image_path]
        characters = {c.id: c for c in state.script.characters.characters}
        spans: List[Dict[str, Any]] = []   # {start, end, sources, kind, char, audio, video}

        first_line_start = segments[0].start_ms if segments else timing.end_ms
        if first_line_start > timing.start_ms:
            if frame.t2v_clip and Path(frame.t2v_clip).exists():
                spans.append({"start": timing.start_ms, "end": first_line_start,
                              "sources": [frame.t2v_clip], "kind": "establishing",
                              "video": True})
            else:
                spans.append({"start": timing.start_ms, "end": first_line_start,
                              "sources": bank if not segments else bank[:1],
                              "kind": "establishing"})

        for i, seg in enumerate(segments):
            end = segments[i + 1].start_ms if i + 1 < len(segments) else timing.end_ms
            char = characters.get(seg.character_id)
            if char is None or char.role == "narrator":
                # Narrator: show the world, rotating through the shot bank.
                spans.append({"start": seg.start_ms, "end": end, "sources": bank,
                              "kind": "establishing"})
                continue
            portrait = frame.portrait_overrides.get(seg.character_id) or \
                (video.portrait_for(seg.character_id).image_path
                 if video.portrait_for(seg.character_id) else bank[0])
            lip_clip = self._lip_sync_clip(state, frame.scene_id, i, portrait, seg) \
                if video.used_lip_sync else None
            if lip_clip:
                spans.append({"start": seg.start_ms, "end": end, "sources": [lip_clip],
                              "kind": "lip_sync", "char": seg.character_id,
                              "audio": seg.file_path, "video": True})
            else:
                # Portrait, with a B-roll cutaway in the middle of long lines.
                sources = [portrait, bank[1 % len(bank)], portrait] \
                    if end - seg.start_ms > MAX_SHOT_MS else [portrait]
                spans.append({"start": seg.start_ms, "end": end, "sources": sources,
                              "kind": "character", "char": seg.character_id,
                              "audio": seg.file_path})

        planned: List[PlannedShot] = []
        for span_idx, span in enumerate(spans):
            pieces = [(span["start"], span["end"])] if span.get("video") \
                else split_span(span["start"], span["end"], MAX_SHOT_MS, MIN_SHOT_MS)
            for j, (a, b) in enumerate(pieces):
                suffix = "est" if span["kind"] == "establishing" and span_idx == 0 \
                    and first_line_start > timing.start_ms else f"s{span_idx + 1}"
                shot_id = f"{frame.scene_id}_{suffix}" + (f"_p{j + 1}" if len(pieces) > 1 else "")
                nominal = span_frames(a, b, fps)
                planned.append(PlannedShot(
                    shot_id=shot_id, kind=span["kind"],
                    source=span["sources"][j % len(span["sources"])],
                    start_ms=a, end_ms=b, nominal_frames=nominal, render_frames=nominal,
                    motion="lip_sync" if span["kind"] == "lip_sync"
                    else pick_motion_for_index(scene.index, len(planned),
                                               shot_kind=span["kind"],
                                               tone=getattr(scene, "tone", "") or ""),
                    character_id=span.get("char"),
                    audio_path=span.get("audio") if j == 0 else None,
                    is_video=bool(span.get("video")),
                ))

        return planned

    def _lip_sync_clip(self, state: PipelineState, scene_id: str, line_idx: int,
                       portrait: str, seg: AudioSegment) -> Optional[str]:
        """Real lip-sync clip from a provider, or None to fall back to stills."""
        if not seg.file_path or not Path(seg.file_path).exists():
            return None
        out = project_dir(state.project_id) / "video" / "lipsync" / f"{scene_id}_l{line_idx + 1}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        res = self.tools.execute(
            "vision.lip_sync", image_path=portrait, audio_path=seg.file_path,
            out_path=str(out), duration_s=seg.duration_ms / 1000.0,
            width=state.video.width, height=state.video.height, fps=state.video.fps,
        )
        if res.success and str(res.metadata.get("provider", "")).startswith(("fal", "replicate")):
            return res.data
        return None

    def _signature(self, planned: List[PlannedShot], video: VideoOutput, effect=None) -> str:
        """Fingerprint of a scene's plan + source files; changes when a re-render is needed."""
        def stamp(path: Optional[str]) -> Any:
            if not path:
                return None
            try:
                st = Path(path).stat()
                return [path, st.st_size, st.st_mtime_ns]
            except OSError:
                return [path, None]
        payload = {
            "fmt": [video.width, video.height, video.fps, video.cinematic_post,
                    SHOT_XFADE_MS, SCENE_XFADE_MS],
            "shots": [[p.kind, stamp(p.source), p.render_frames, p.motion, p.is_video,
                       p.framing, p.crop] for p in planned],
        }
        if not (video.legacy_transitions and not any(p.custom_out for p in planned)):
            # A film rendered before DST-39, with the transitions it was cut with, keeps the
            # fingerprint it had, so editing it does not draw every scene again. Anything else
            # (a new film, or a transition the creator chose) is part of what a scene looks like.
            payload["out"] = [[p.out_kind, p.out_frames] for p in planned]
        if effect:
            payload["fx"] = [effect.kind, effect.strength]    # a scene with a look is a different scene
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _to_shot(scene_id: str, p: PlannedShot) -> Shot:
        return Shot(
            shot_id=p.shot_id, scene_id=scene_id,
            kind=p.kind if p.kind in ("establishing", "character", "lip_sync") else "broll",
            character_id=p.character_id, image_path=p.source, clip_path=None,
            duration_ms=p.end_ms - p.start_ms, motion=p.motion, audio_path=p.audio_path,
            start_ms=p.start_ms, render_frames=p.render_frames,
            framing=p.framing, crop=list(p.crop) if p.crop else None,
            out_kind=p.out_kind, out_frames=p.out_frames,
        )

    # ---- rendering -------------------------------------------------------

    def _render_scenes(self, state: PipelineState,
                       scenes: List[Tuple[str, List[PlannedShot]]]) -> Dict[str, Path]:
        """Render these scenes' shots, several at once, then cut each scene."""
        video = state.video
        proj = project_dir(state.project_id)
        shot_dir = proj / "video" / "shots"
        scene_dir = proj / "video" / "scenes"
        shot_dir.mkdir(parents=True, exist_ok=True)
        scene_dir.mkdir(parents=True, exist_ok=True)

        story = state.script.story if state.script else None
        visual_style = getattr(story, "visual_style", "") or ""
        tones = {sc.scene_id: getattr(sc, "tone", "") or ""
                 for sc in (state.script.scenes if state.script else [])}

        # One pool for every shot of every scene: a pool per scene would leave
        # cores idle while a 3-shot scene finished its last shot.
        jobs = []
        for scene_id, planned in scenes:
            for p in planned:
                rs = RenderShot(image_path=p.source, duration_ms=p.end_ms - p.start_ms,
                                motion=p.motion, frames=p.render_frames,
                                is_lip_sync=p.is_video, visual_style=visual_style,
                                tone=tones.get(scene_id, ""), crop=p.crop,
                                seed=zlib.crc32(p.shot_id.encode()))
                jobs.append((render_shot, (rs, shot_dir / f"{p.shot_id}.mp4",
                                           video.width, video.height, video.fps,
                                           video.cinematic_post, video.cinematic_post)))
        paths = iter(run_jobs(jobs, workers=shot_workers(), label="shots", step="shots"))

        clips: Dict[str, Path] = {}
        effects = {sc.scene_id: sc.effect for sc in (state.script.scenes if state.script else [])
                   if sc.effect}
        looks_total = sum(1 for scene_id, _planned in scenes if scene_id in effects)
        looks_done = 0
        for scene_id, planned in scenes:
            progress_hook.pulse()
            clips[scene_id] = assemble_scene(
                [next(paths) for _ in planned], scene_dir / f"{scene_id}.mp4",
                durations_s=[p.render_frames / video.fps for p in planned],
                boundaries=[(transitions.xfade_name(p.out_kind), p.out_frames / video.fps)
                            for p in planned[:-1]],
            )
            log.info("  scene %s composed (%d shots)", scene_id, len(planned))
            if scene_id in effects:
                # Drawn over the finished scene, frame by frame; same frame count out as in.
                from shared import finish
                raw = clips[scene_id]
                progress_hook.report("looks", looks_done, looks_total)
                done = finish.apply(raw, scene_dir / f"{scene_id}_fx.mp4", effects[scene_id].kind,
                                    effects[scene_id].strength, video.fps)
                done.replace(raw)
                looks_done += 1
                progress_hook.report("looks", looks_done, looks_total)
                log.info("  scene %s: %s (%s)", scene_id, effects[scene_id].kind, effects[scene_id].strength)
        return clips

    def _compose_final(self, state: PipelineState) -> Path:
        video = state.video
        proj = project_dir(state.project_id)
        fps = video.fps

        clips, durations, boundaries = [], [], []
        for f in video.frames:
            # A scene's clip is its shots less the overlaps inside it; its last shot's own
            # overlap (into the next scene) is what the final join blends.
            frames = sum(s.render_frames for s in f.shots) - sum(s.out_frames for s in f.shots[:-1])
            clips.append(f.clip_path)
            durations.append(frames / fps)
            last = f.shots[-1] if f.shots else None
            boundaries.append((transitions.xfade_name(last.out_kind) if last else None,
                               (last.out_frames if last else 0) / fps))
        boundaries = boundaries[:-1]

        out = proj / "final_output.mp4"
        master = state.audio.master_track if state.audio else None
        # Only the last time the film is encoded gets the delivered quality; a pass
        # that another one follows (a speed change, burned subtitles) is near
        # transparent so the passes stop compounding their loss (DST-35).
        speed = abs(video.speed_factor - 1.0) > 1e-3
        burn = self._will_burn_subtitles(state)
        ass = self._graphics_script(state, proj, subtitles=burn)
        self._graphics_done = False
        compose = self.tools.execute(
            "video.compose", clips=clips, out_path=str(out), audio_path=master,
            transition="fade", durations_s=durations, boundaries=boundaries,
            quality="intermediate" if (speed or burn or ass) else "final",
        )
        if not compose.success:
            raise RuntimeError(f"final compose failed: {compose.error}")
        if compose.metadata.get("fallback"):
            log.warning("crossfade compose failed; fell back to hard cuts — "
                        "audio may drift by up to %.1fs",
                        sum(d for _n, d in boundaries))

        final = out
        if speed:
            final = self._apply_speed(out, proj / "final_output_speed.mp4",
                                      video.speed_factor, has_audio=bool(master),
                                      quality="intermediate" if (burn or ass) else "final")

        video.subtitle_languages = []
        video.subtitles_skipped = []
        if video.has_subtitles and state.audio:
            subbed = self._embed_subtitles(state, final, graphics_ass=ass if burn else None)
            if subbed:
                final = subbed
        if ass and not self._graphics_done:
            # Subtitles are off (or were not burned): the titles get a pass of their own, the
            # last encode, at the delivered quality.
            final = self._burn_graphics(final, proj / "final_output_graphics.mp4", ass)
        return final

    def _graphics_script(self, state: PipelineState, proj: Path, subtitles: bool = False) -> Optional[Path]:
        """Write the titles' ASS script for this film, or None when none are switched on."""
        from shared import graphics
        if not (getattr(state, "script", None) and getattr(state, "audio", None)):
            return None
        video = state.video
        text = graphics.build_ass(state.script, state.audio.manifest, video.width, video.height,
                                  speed=video.speed_factor, language=video.subtitle_language or "English",
                                  subtitles=subtitles)
        if not text:
            return None
        path = proj / "graphics.ass"
        path.write_text(text, encoding="utf-8")
        return path

    def _burn_graphics(self, src: Path, dst: Path, ass: Path) -> Path:
        from shared import graphics
        # Keep every stream: soft subtitle tracks ride along when the film has them.
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", graphics.filter_for(ass),
               "-map", "0:v", "-map", "0:a?", "-map", "0:s?",
               *video_quality.x264_args("final"), "-c:a", "copy", "-c:s", "copy", str(dst)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"burning the titles failed: {proc.stderr[-800:]}")
        log.info("burned titles into the picture")
        return dst

    @staticmethod
    def _will_burn_subtitles(state: PipelineState) -> bool:
        """Will `_embed_subtitles` burn text into the picture (one more encode)?
        It does when subtitles are on, burning is asked for and there are lines."""
        video = state.video
        if not (video.has_subtitles and video.burn_subtitles and state.audio):
            return False
        return any(s.kind == "dialogue" and s.text for s in state.audio.manifest.segments)

    @staticmethod
    def _apply_speed(src: Path, dst: Path, factor: float, has_audio: bool,
                     quality: str = "final") -> Path:
        """ffmpeg setpts (video) + atempo chain (audio, bounded to [0.5, 2] per stage)."""
        af_chain = []
        atempo = factor
        while atempo > 2.0:
            af_chain.append("atempo=2.0")
            atempo /= 2.0
        while atempo < 0.5:
            af_chain.append("atempo=0.5")
            atempo *= 2.0
        af_chain.append(f"atempo={atempo:.4f}")
        fc = f"[0:v]setpts={1 / factor:.6f}*PTS[v]"
        cmd = ["ffmpeg", "-y", "-i", str(src)]
        if has_audio:
            fc += f";[0:a]{','.join(af_chain)}[a]"
            cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-filter_complex", fc, "-map", "[v]", "-an"]
        cmd += [*video_quality.x264_args(quality), str(dst)]
        subprocess.run(cmd, check=True, capture_output=True)
        return dst

    # ---- subtitles -------------------------------------------------------

    def _embed_subtitles(self, state: PipelineState, video_path: Path,
                         graphics_ass: Optional[Path] = None) -> Optional[Path]:
        """Put subtitles where players will actually show them.

        Most players ignore soft `mov_text` tracks inside an MP4 unless the
        viewer digs into a menu, so by default the chosen language is **burned
        into the picture** and the remaining languages ride along as switchable
        tracks. Sidecar .srt/.vtt files are written next to the video too: VLC
        loads a same-named .srt automatically and the web player uses the .vtt.

        A language whose translation fails is skipped (never shipped as English
        under a foreign label). Translations are cached on the VideoOutput and
        reused until the English lines change.
        """
        video = state.video
        segs = [s for s in state.audio.manifest.segments if s.kind == "dialogue" and s.text]
        if not segs:
            return None
        english = [s.text for s in segs]
        if video.subtitle_source != english:
            video.subtitle_source = english
            video.subtitle_cache = {}

        tracks: Dict[str, List[Dict[str, Any]]] = {}
        speed = video.speed_factor
        for lang in self._subtitle_languages(video):
            texts = english if lang == "English" else video.subtitle_cache.get(lang)
            if texts is None or len(texts) != len(english):
                res = self.tools.execute("text.translate", lines=english, target_language=lang)
                if not res.success:
                    log.warning("subtitle translation to %s failed (%s) — track skipped",
                                lang, res.error)
                    video.subtitles_skipped.append(lang)
                    continue
                texts = res.data
                video.subtitle_cache[lang] = texts
                log.info("translated %d subtitle lines to %s via %s",
                         len(texts), lang, res.metadata.get("provider"))
            tracks[lang] = [{"start_ms": int(s.start_ms / speed), "end_ms": int(s.end_ms / speed),
                             "text": t} for s, t in zip(segs, texts)]

        if not tracks:
            return None
        proj = project_dir(state.project_id)
        self._write_sidecars(proj, tracks)

        final = video_path
        burned = None
        chosen = video.subtitle_language if video.subtitle_language in tracks else "English"
        if video.burn_subtitles:
            res = self.tools.execute(
                "video.subtitle", in_path=str(final),
                out_path=str(proj / "final_output_subtitled.mp4"),
                lines=tracks[chosen], language=chosen,
                layout=film_format.subtitle_layout(film_format.format_of_size(video.width, video.height),
                                                   video.width, video.height),
                frame=(video.width, video.height),
                graphics_ass=str(graphics_ass) if graphics_ass else None,
            )
            if res.success:
                final, burned = Path(res.data), chosen
                self._graphics_done = bool(graphics_ass)
                log.info("burned %s subtitles into the picture", chosen)
            else:
                log.warning("subtitle burn-in failed: %s — falling back to soft tracks only",
                            res.error)
        video.burned_subtitle_language = burned

        # The burned language would double up if it were also a soft track.
        soft = {lang: lines for lang, lines in tracks.items() if lang != burned}
        if not soft:
            video.subtitle_languages = [burned] if burned else []
            return final

        out = proj / "final_output_multilang.mp4"
        res = self.tools.execute("video.multi_subtitle", in_path=str(final),
                                 out_path=str(out), tracks=soft,
                                 default_language=chosen if chosen in soft else
                                 next(iter(soft)))
        if not res.success:
            log.warning("subtitle embed failed: %s — keeping %s", res.error, final.name)
            video.subtitle_languages = [burned] if burned else []
            return final if burned else None
        video.subtitle_languages = ([burned] if burned else []) +             list(res.metadata.get("languages", []))
        log.info("subtitles: %s burned in, %s as switchable tracks",
                 burned or "none", ", ".join(res.metadata.get("languages", [])) or "none")
        return out

    @staticmethod
    def _write_sidecars(proj: Path, tracks: Dict[str, List[Dict[str, Any]]]) -> None:
        """Sidecar subtitle files: VLC auto-loads the .srt, the web player uses the .vtt."""
        from mcp.tools.video_tools.subtitle_tool import SubtitleTool, to_webvtt
        from shared.languages import iso639_1
        sub_dir = proj / "subtitles"
        sub_dir.mkdir(parents=True, exist_ok=True)
        for lang, lines in tracks.items():
            srt = SubtitleTool._build_srt(lines)
            (sub_dir / f"{lang.lower()}.srt").write_text(srt, encoding="utf-8")
            (sub_dir / f"{lang.lower()}.vtt").write_text(to_webvtt(lines), encoding="utf-8")
            # Same-named sidecars beside the MP4 so players pick them up on open.
            code = iso639_1(lang)
            for stem in ("final_output", "final_output_multilang", "final_output_subtitled"):
                (proj / f"{stem}.{code}.srt").write_text(srt, encoding="utf-8")

    @staticmethod
    def _subtitle_languages(video: VideoOutput) -> List[str]:
        """English, the chosen language, then any SUBTITLE_EXTRA_LANGUAGES from .env."""
        wanted = ["English", video.subtitle_language]
        wanted += [s for s in os.getenv("SUBTITLE_EXTRA_LANGUAGES", "").split(",") if s.strip()]
        out: List[str] = []
        for name in wanted:
            lang = canonical(name)
            if not lang:
                log.warning("ignoring unsupported subtitle language %r", name)
            elif lang not in out:
                out.append(lang)
        return out


def _replace_file(src: Path, dst: Path) -> None:
    """Move `src` over `dst` in one step. Windows refuses while a reader has `dst`
    open (a browser fetching that very picture), so try again briefly."""
    for attempt in range(5):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2)


def _ensure_image(res, out, prompt: str, width: int, height: int) -> str:
    """Return a path that definitely has an image behind it.

    Every provider can fail at once — free image endpoints run out of quota on
    the same day — and the agent used to record the path it had intended to
    write. The missing file then surfaced much later, as ffmpeg failing to
    open its input half way through a render.
    """
    if res.success and res.data and Path(res.data).exists():
        return res.data
    if not Path(out).exists():
        from mcp.tools.vision_tools.image_gen_tool import write_placeholder
        log.warning("  no image was produced for %s — drawing a placeholder", Path(out).name)
        write_placeholder(prompt, out, width, height)
    return str(out)


def _seed(prompt: str, salt: str = "") -> int:
    """Stable per-prompt seed; a salt (e.g. the version) gives a fresh variation."""
    return int(hashlib.md5(f"{prompt}|{salt}".encode()).hexdigest()[:8], 16) % 2**31
