"""Phase 1 agent: prompt -> validated ScriptOutput.

One structured call writes the story, the cast and the scenes together,
against the ScriptOutput schema, with a word budget derived from the target
length. The `story` provider chain supplies the model: malformed JSON is
retried with the validation error, a provider that is down hands over to the
next, and with none left (or no keys at all) a deterministic template writes
the script. Then every speaker is checked against the cast and each
character's look is pinned before any image is drawn.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Tuple

from mcp.tools.llm_tools.llm_client import get_llm_client
from shared.constants import PHASE_STORY, film_length_tolerance, script_length_repairs
from shared.schemas.pipeline import PipelineState
from shared.schemas.story import Character, ScriptOutput
from shared.timeline import (
    WORDS_PER_SECOND, expected_scene_overhead_ms, planned_length_ms, word_budget,
)
from shared.utils.files import project_dir, write_json
from shared.utils.logging import get_logger

from .appearance import lock_appearances
from .planner import template_script

log = get_logger("story_agent")


SYSTEM_PROMPT = """You are a master screenwriter and story architect.
Your job is to transform a user's one-line prompt into a complete short film script
with characters, scenes, and dialogue. You must respond with a single valid JSON
object that matches the requested schema EXACTLY. No prose, no markdown fences."""


SCRIPT_USER_PROMPT = """User prompt: "{prompt}"

Target duration: {duration_s} seconds (about {scene_count} scenes of equal length).

Generate a complete script JSON object with this exact structure:
{{
  "story": {{
    "project_id": "{project_id}",
    "title": "...",
    "logline": "one sentence pitch",
    "synopsis": "one paragraph summary",
    "genre": "...",
    "visual_style": "how the whole film should look: medium, palette, lighting, lens — "
                    "e.g. 'gritty cinematic sci-fi, cold teal palette, volumetric light, 35mm'",
    "themes": ["...", "..."],
    "arc": "three-act",
    "target_duration_s": {duration_s}
  }},
  "characters": {{
    "characters": [
      {{
        "id": "char_narrator",
        "name": "Narrator",
        "role": "narrator",
        "description": "one sentence personality",
        "visual_description": "appearance details for image generation, very specific",
        "voice_style": "warm/whispered/deep/cheerful/anxious/etc",
        "voice_gender": "male|female|neutral",
        "voice_age": "child|young|adult|elderly"
      }}
    ]
  }},
  "scenes": [
    {{
      "scene_id": "scene_1",
      "index": 0,
      "title": "...",
      "setting": "where + when",
      "tone": "neutral|tense|joyful|melancholic",
      "visual_prompt": "detailed image-gen prompt with subject, setting, lighting, camera angle",
      "camera": "wide shot / medium / close-up / aerial",
      "duration_ms": {scene_duration_ms},
      "dialogue": [
        {{
          "line_id": "scene_1_l1",
          "character_id": "char_narrator",
          "text": "spoken line",
          "emotion": "reflective",
          "duration_ms": 4000
        }}
      ],
      "music_mood": "ambient|tense|epic|mysterious|joyful|sad|ethereal",
      "transition_in": "fade|cut|slide|zoom"
    }}
  ]
}}

Rules:
- Include 3-4 characters total, each with a consistent visual + voice identity.
- Generate {scene_count} total scenes forming a clear arc (intro -> rising -> climax -> resolution).
- Every dialogue line's character_id MUST match a character.id from the roster.
- Visual prompts should be self-contained and image-gen friendly.
- Total of all scene duration_ms should be roughly {total_ms}.
- Keep ALL spoken dialogue together to about {word_budget} words, which is
  about {scene_words} words per scene (people speak ~{words_per_second} words per second,
  and each scene also needs ~{scene_overhead_s} seconds without speech). This is what makes
  the film land near {duration_s} seconds. Fewer words make a shorter film, so do not stop short.
- `visual_style` must suit the story you are telling (a horror short and a children's
  fable should not look the same). Every image in the film is generated with it, so make
  it concrete about medium, palette and lighting — and do not mention characters in it.
- Make the dialogue feel natural and specific to the genre.
- The protagonist should appear in every scene; supporting characters can vary.
"""


LENGTH_REPAIR_PROMPT = """Your script's dialogue is too {direction}: the film would run about {planned_s} seconds and it must run {target_s} seconds. It has {words} words of dialogue. {verb} about {change} words, so that it has about {goal} words in all, spread across the {scene_count} scenes.

Keep exactly the same characters (same ids), the same {scene_count} scenes in the same order and the same story. Change only the spoken lines: {how}. Every scene keeps at least one line, and every line's character_id must still be one of the characters.
Answer with the complete script JSON again, in exactly the same structure as the script below.

Current script:
{script}"""


def script_planned_ms(script: ScriptOutput) -> int:
    """The film's length as the timeline plans it from this script's lines."""
    return planned_length_ms([[ln.text for ln in scene.dialogue] for scene in script.scenes])


def repair_prompt(script: ScriptOutput, planned_ms: int, target_s: int) -> str:
    """Ask for a stated number of words more or fewer, from the same script."""
    words = sum(len(ln.text.split()) for scene in script.scenes for ln in scene.dialogue)
    short = planned_ms < target_s * 1000
    change = max(1, round(abs(target_s * 1000 - planned_ms) / 1000 * WORDS_PER_SECOND))
    return LENGTH_REPAIR_PROMPT.format(
        direction="short" if short else "long",
        planned_s=round(planned_ms / 1000), target_s=target_s, words=words,
        verb="Add" if short else "Cut", change=change,
        goal=words + change if short else max(1, words - change),
        scene_count=len(script.scenes),
        how=("lengthen lines or add a line where a scene can bear it" if short
             else "shorten lines or drop a line where a scene can spare it"),
        script=script.model_dump_json(),
    )


def _changed_shape(before: ScriptOutput, after: ScriptOutput) -> Optional[str]:
    """Why `after` is not the same film as `before`, or None if it is: the repair
    may change what is said, not who says it or how the film is cut into scenes."""
    if {c.id for c in before.characters.characters} != {c.id for c in after.characters.characters}:
        return "the cast changed"
    if [s.scene_id for s in before.scenes] != [s.scene_id for s in after.scenes]:
        return "the scenes changed"
    known = {c.id for c in after.characters.characters}
    if any(ln.character_id not in known for s in after.scenes for ln in s.dialogue):
        return "a line has a speaker outside the cast"
    if any(old.dialogue and not new.dialogue for old, new in zip(before.scenes, after.scenes)):
        return "a scene lost all its lines"
    return None


def length_check(script: ScriptOutput, target_s: int, repairs: int, written_by: str) -> dict:
    """What the length check found, kept on the project so a reload and the job's
    completion event can both say it (`within` false means: flag it)."""
    planned_ms = script_planned_ms(script)
    target_ms = target_s * 1000
    tolerance = film_length_tolerance()
    return {
        "planned_s": round(planned_ms / 1000, 1),
        "target_s": target_s,
        "error": round((planned_ms - target_ms) / target_ms, 3),
        "tolerance": tolerance,
        "repairs": repairs,
        "written_by": written_by,
        "within": abs(planned_ms - target_ms) <= tolerance * target_ms,
    }


def current_length_check(state: PipelineState) -> Optional[dict]:
    """The length check of the script as it is now. Creators edit lines in the
    storyboard, so the check stored at writing time can be stale; only how many
    repairs it took and who wrote it are carried over from that record."""
    if not state.script:
        return None
    was = state.metadata.get("length_check") or {}
    return length_check(state.script, state.script.story.target_duration_s,
                        was.get("repairs", 0), was.get("written_by", "model"))


class StoryAgent:
    def __init__(self):
        self.llm = get_llm_client("story")

    # ---- public ----------------------------------------------------------

    def run(self, state: PipelineState, target_duration_s: int = 45,
            scene_count: int = 4) -> ScriptOutput:
        log.info("phase 1 start (provider=%s, project=%s)",
                 self.llm.provider, state.project_id)
        state.phase1.status = "running"
        state.phase1.started_at = datetime.utcnow().isoformat()

        try:
            script, repairs, written_by = self._generate(
                state.project_id, state.user_prompt, target_duration_s, scene_count)
            self._validate(script)
            state.metadata["length_check"] = length_check(
                script, target_duration_s, repairs, written_by)
            # Pin each character's look before any image is drawn.
            lock_appearances(script)
            artifacts = self._serialize(state.project_id, script)
            state.script = script
            state.phase1.status = "complete"
            state.phase1.finished_at = datetime.utcnow().isoformat()
            state.phase1.artifact_paths = artifacts
            log.info("phase 1 complete (%d characters, %d scenes)",
                     len(script.characters.characters), len(script.scenes))
            return script
        except Exception as e:  # noqa: BLE001
            state.phase1.status = "failed"
            state.phase1.error = f"{type(e).__name__}: {e}"
            log.exception("phase 1 failed")
            raise

    def serialize(self, state: PipelineState) -> list[str]:
        """Re-write the script artifacts after an edit and refresh artifact paths."""
        state.phase1.artifact_paths = self._serialize(state.project_id, state.script)
        return state.phase1.artifact_paths

    # ---- generation ------------------------------------------------------

    def _generate(self, project_id: str, prompt: str,
                  duration_s: int, scene_count: int) -> Tuple[ScriptOutput, int, str]:
        """The script, how many length repairs it took, and who wrote it
        ("model" or "template")."""
        # Mock provider -> deterministic template fallback.
        if self.llm.provider == "mock":
            log.info("using template-based fallback (no LLM provider configured)")
            return (template_script(project_id, prompt, target_duration_s=duration_s,
                                    scene_count=scene_count), 0, "template")

        scene_duration_ms = (duration_s * 1000) // scene_count
        total_ms = scene_duration_ms * scene_count
        total_words, scene_words = word_budget(duration_s, scene_count)
        user_prompt = SCRIPT_USER_PROMPT.format(
            prompt=prompt,
            duration_s=duration_s,
            scene_count=scene_count,
            scene_duration_ms=scene_duration_ms,
            total_ms=total_ms,
            word_budget=total_words,
            scene_words=scene_words,
            words_per_second=WORDS_PER_SECOND,
            scene_overhead_s=round(expected_scene_overhead_ms() / 1000, 1),
            project_id=project_id,
        )
        try:
            script = self.llm.generate_structured(
                prompt=user_prompt,
                schema=ScriptOutput,
                system=SYSTEM_PROMPT,
                temperature=0.8,
                max_tokens=4000,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("structured LLM gen failed (%s) — using template fallback", e)
            return (template_script(project_id, prompt, target_duration_s=duration_s,
                                    scene_count=scene_count), 0, "template")
        # The length check, and the warning built on it, are against what was
        # asked for; the model's own echo of it in the script is not trusted.
        script.story.target_duration_s = duration_s
        script, repairs = self._fit_length(script, duration_s)
        return script, repairs, "model"

    def _fit_length(self, script: ScriptOutput, duration_s: int) -> Tuple[ScriptOutput, int]:
        """Send a script whose planned length is outside the tolerance back to the
        model to add or cut a stated number of words, up to `script_length_repairs()`
        times. The best script seen wins, so a repair can never make it worse; a
        provider that fails, or an answer that changes the cast or the scenes, ends
        or wastes that repair but never replaces the script (and never falls back
        to the template, which would throw the story away)."""
        target_ms = duration_s * 1000
        allowed_ms = film_length_tolerance() * target_ms
        best, best_off = script, abs(script_planned_ms(script) - target_ms)
        repairs, limit = 0, script_length_repairs()
        while repairs < limit and best_off > allowed_ms:
            repairs += 1
            planned_ms = script_planned_ms(best)
            log.info("script plans %.1f s for a %d s target; repair %d of %d",
                     planned_ms / 1000, duration_s, repairs, limit)
            try:
                candidate = self.llm.generate_structured(
                    prompt=repair_prompt(best, planned_ms, duration_s),
                    schema=ScriptOutput,
                    system=SYSTEM_PROMPT,
                    temperature=0.5,
                    max_tokens=4000,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("length repair %d failed (%s); keeping the script as it is", repairs, e)
                break
            problem = _changed_shape(best, candidate)
            if problem:
                log.warning("length repair %d discarded: %s", repairs, problem)
                continue
            candidate.story.project_id = best.story.project_id
            candidate.story.target_duration_s = duration_s
            off = abs(script_planned_ms(candidate) - target_ms)
            if off < best_off:
                best, best_off = candidate, off
        return best, repairs

    # ---- validation --------------------------------------------------------

    def _validate(self, script: ScriptOutput) -> None:
        char_ids = {c.id for c in script.characters.characters}
        for scene in script.scenes:
            for line in scene.dialogue:
                if line.character_id not in char_ids:
                    raise ValueError(
                        f"unknown character_id {line.character_id} "
                        f"in {scene.scene_id}/{line.line_id}"
                    )
        if not script.scenes:
            raise ValueError("scenes must not be empty")

    # ---- serialization: the artifacts each phase hands to the next --------

    def _serialize(self, project_id: str, script: ScriptOutput) -> list[str]:
        proj = project_dir(project_id)
        story_path = proj / "story.json"
        chars_path = proj / "characters.json"
        script_path = proj / "script.json"
        audio_handoff = proj / "phase2_audio_handoff.json"
        video_handoff = proj / "phase3_video_handoff.json"
        summary_path = proj / "summary.json"

        write_json(story_path, script.story.model_dump(mode="json"))
        write_json(chars_path, script.characters.model_dump(mode="json"))
        write_json(script_path, [s.model_dump(mode="json") for s in script.scenes])

        # Phase 2 hand-off: per-character voice configs + per-line text.
        audio_handoff_data = {
            "project_id": project_id,
            "voice_configs": [
                {
                    "character_id": c.id,
                    "engine": "gtts",
                    "language": "en",
                    "tld": _tld_for(c),
                    "voice_style": c.voice_style,
                    "voice_gender": c.voice_gender,
                    "voice_age": c.voice_age,
                } for c in script.characters.characters
            ],
            "segments": [
                {
                    "scene_id": s.scene_id,
                    "line_id": ln.line_id,
                    "character_id": ln.character_id,
                    "text": ln.text,
                    "emotion": ln.emotion,
                    "duration_ms": ln.duration_ms,
                } for s in script.scenes for ln in s.dialogue
            ],
            "music_moods": [
                {"scene_id": s.scene_id, "mood": s.music_mood, "duration_ms": s.duration_ms}
                for s in script.scenes
            ],
        }
        write_json(audio_handoff, audio_handoff_data)

        # Phase 3 hand-off: per-scene visual prompts + camera + transitions.
        video_handoff_data = {
            "project_id": project_id,
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "index": s.index,
                    "visual_prompt": s.visual_prompt,
                    "camera": s.camera,
                    "duration_ms": s.duration_ms,
                    "transition_in": s.transition_in,
                    "subtitles": [
                        {"line_id": ln.line_id, "text": ln.text,
                         "duration_ms": ln.duration_ms}
                        for ln in s.dialogue
                    ],
                } for s in script.scenes
            ],
        }
        write_json(video_handoff, video_handoff_data)

        # Run summary.
        write_json(summary_path, {
            "project_id": project_id,
            "phase": PHASE_STORY,
            "status": "complete",
            "title": script.story.title,
            "scene_count": len(script.scenes),
            "character_count": len(script.characters.characters),
            "total_duration_ms": script.total_duration_ms(),
            "artifacts": [str(story_path), str(chars_path), str(script_path),
                          str(audio_handoff), str(video_handoff)],
        })
        return [str(story_path), str(chars_path), str(script_path),
                str(audio_handoff), str(video_handoff), str(summary_path)]


def _tld_for(c: Character) -> str:
    """Pick a gTTS regional accent that varies voices across the cast."""
    if c.voice_gender == "female":
        return "co.uk"
    if c.voice_age == "elderly":
        return "co.in"
    if c.role == "narrator":
        return "com"
    return "com.au"
