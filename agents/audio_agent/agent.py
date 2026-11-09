"""Phase 2 agent: ScriptOutput -> AudioOutput.

Per-line TTS with character-consistent voices, then the film's timeline
(shared/timeline.py), per-scene BGM sized to that timeline, and a master track
with every line placed at its timeline position. Emits the structured
AudioOutput and a flat timing_manifest.json.

The three steps are public so edits can reuse them:
    render_line()  -> (re)record one dialogue line
    retime()       -> recompute the timeline from current segment durations
    remix()        -> rebuild BGM + master from the timeline
"""
from __future__ import annotations
import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from mcp.tool_executor import ToolExecutor
from shared import music, providers
from shared.constants import PHASE_AUDIO
from shared.languages import SPEECH_VOICES, spoken_language
from shared.schemas.audio import (
    AudioOutput, AudioSegment, SceneTiming, TimingManifest, VoiceConfig,
)
from shared.schemas.pipeline import PipelineState
from shared.providers import ProviderSpec
from shared.schemas.story import Character, ScriptOutput
from shared.timeline import build_timeline, estimate_line_ms
from shared.utils.files import project_dir, write_json
from shared.utils.parallel import run_jobs
from shared.utils.logging import get_logger

log = get_logger("audio_agent")

DEFAULT_VOICE = "en-US-GuyNeural"
_SILENT = ProviderSpec(role="tts", provider="silent")
# The mix is set relative to the dialogue (mcp/tools/audio_tools/audio_merger.py, DST-41): music_volume 0.32,
# ambience_volume 0.22 and voice_volume 1.0 are "100%", the multipliers on levels fixed in LUFS, not absolute gains.

# Kokoro voices (open-source engine): af_/am_ are US, bf_/bm_ British.
KOKORO_VOICES = {
    "narrator": "bm_george",
    "child": "af_nicole",
    "elderly": "bm_lewis",
    "female": "af_heart",
    "male": "am_michael",
    "neutral": "af_bella",
}
KOKORO_POOL = {
    "female": ["af_heart", "af_bella", "bf_emma", "af_nicole"],
    "male": ["am_michael", "am_adam", "bm_george", "bm_lewis"],
    "neutral": ["af_bella", "bm_george", "af_heart", "am_adam"],
}

# Alternate edge-tts voices per gender, used by "change voice" edits.
VOICE_POOL = {
    "female": ["en-US-AriaNeural", "en-US-JennyNeural", "en-GB-SoniaNeural",
               "en-AU-NatashaNeural"],
    "male": ["en-US-GuyNeural", "en-US-ChristopherNeural", "en-GB-RyanNeural",
             "en-AU-WilliamNeural"],
    "neutral": ["en-US-ChristopherNeural", "en-US-AriaNeural", "en-GB-RyanNeural",
                "en-US-JennyNeural"],
}

# Tone presets -> prosody (rate in wpm, pitch in Hz, volume multiplier).
TONE_PRESETS: Dict[str, Dict[str, Any]] = {
    "whispered": {"rate": 140, "pitch": -5, "volume": 0.7},
    "soft":      {"rate": 160, "pitch": -2, "volume": 0.75},
    "deep":      {"rate": 160, "pitch": -15, "volume": 1.0},
    "warm":      {"rate": 165, "pitch": -3, "volume": 1.0},
    "sad":       {"rate": 150, "pitch": -8, "volume": 0.85},
    "cheerful":  {"rate": 195, "pitch": 8, "volume": 1.05},
    "anxious":   {"rate": 200, "pitch": 5, "volume": 1.0},
    "angry":     {"rate": 190, "pitch": 4, "volume": 1.2},
    "loud":      {"rate": 175, "pitch": 0, "volume": 1.3},
}
# The words people (and writing models) use for those tones.
TONE_ALIASES = {
    "whisper": "whispered", "whispering": "whispered", "whispery": "whispered",
    "softer": "soft", "gentle": "soft", "deeper": "deep", "warmer": "warm",
    "happy": "cheerful", "cheery": "cheerful", "cheer": "cheerful",
    "nervous": "anxious", "louder": "loud", "sadder": "sad",
}


class RenderedLine(NamedTuple):
    path: str
    duration_ms: int
    engine: str             # who actually spoke it, after any fallback
    voice: Optional[str]


class AudioAgent:
    def __init__(self):
        self.tools = ToolExecutor()

    # ---- public ----------------------------------------------------------

    def run(self, state: PipelineState, with_bgm: bool = True,
            tts_engine: Optional[str] = None) -> AudioOutput:
        if not state.script:
            raise ValueError("phase 2 requires state.script (run phase 1 first)")
        # Which voice engine serves this run comes from config/providers.yaml.
        tts_engine = tts_engine or (providers.active("tts") or _SILENT).provider
        log.info("phase 2 start (project=%s, engine=%s)", state.project_id, tts_engine)
        state.phase2.status = "running"
        state.phase2.started_at = datetime.utcnow().isoformat()

        try:
            voice_configs = self._build_voice_configs(state.script, default_engine=tts_engine)
            cfg_by_char = {v.character_id: v for v in voice_configs}

            lines = [(scene, line) for scene in state.script.scenes for line in scene.dialogue]
            rendered = run_jobs(
                [(self.render_line,
                  (state.project_id, scene.scene_id, line.line_id, line.text,
                   cfg_by_char.get(line.character_id), line.duration_ms))
                 for scene, line in lines],
                workers=providers.concurrency("tts"), label="tts lines", step="voices",
            )
            segments: List[AudioSegment] = [
                AudioSegment(
                    segment_id=f"{scene.scene_id}_{line.line_id}",
                    scene_id=scene.scene_id, line_id=line.line_id,
                    character_id=line.character_id, file_path=out.path,
                    kind="dialogue", start_ms=0, end_ms=out.duration_ms,
                    duration_ms=out.duration_ms, text=line.text,
                    engine=out.engine, voice=out.voice,
                )
                for (scene, line), out in zip(lines, rendered)
            ]

            state.audio = AudioOutput(
                voice_configs=voice_configs,
                manifest=TimingManifest(project_id=state.project_id,
                                        total_duration_ms=0, segments=segments),
                bgm_enabled=with_bgm,
            )
            self.retime(state)
            self.remix(state)

            state.phase2.status = "complete"
            state.phase2.finished_at = datetime.utcnow().isoformat()
            state.phase2.artifact_paths = self._serialize(state.project_id, state.audio)
            log.info("phase 2 complete (%d dialogue segments, %.1fs timeline, master=%s)",
                     len(segments), state.audio.manifest.total_duration_ms / 1000,
                     state.audio.master_track)
            return state.audio
        except Exception as e:  # noqa: BLE001
            state.phase2.status = "failed"
            state.phase2.error = f"{type(e).__name__}: {e}"
            log.exception("phase 2 failed")
            raise

    def render_line(self, project_id: str, scene_id: str, line_id: str, text: str,
                    cfg: Optional[VoiceConfig], fallback_ms: int = 2500) -> RenderedLine:
        """Record one dialogue line with its character's voice."""
        audio_dir = project_dir(project_id) / "audio"
        audio_dir.mkdir(exist_ok=True, parents=True)
        out_file = audio_dir / f"{scene_id}_{line_id}.mp3"
        res = self.tools.execute(
            "audio.tts",
            text=text,
            out_path=str(out_file),
            engine=cfg.engine if cfg else "gtts",
            language=cfg.language if cfg else "en",
            tld=cfg.tld if cfg else "com",
            rate=cfg.rate if cfg else 175,
            pitch=cfg.pitch if cfg else 0,
            volume=cfg.volume if cfg else 1.0,
            voice=(cfg.voice_id if cfg and cfg.voice_id else DEFAULT_VOICE),
        )
        if res.success:
            rendered = Path(res.data)
            engine, voice = res.metadata.get("engine", "unknown"), res.metadata.get("voice")
        else:
            log.warning("tts failed for %s: %s — using silent placeholder", line_id, res.error)
            rendered = audio_dir / f"{scene_id}_{line_id}.wav"
            self.tools.execute("audio.tts", text=text, out_path=str(rendered), engine="silent")
            engine, voice = "silent", None
        duration = self._probe_duration_ms(rendered) or fallback_ms or estimate_line_ms(text)
        return RenderedLine(str(rendered), duration, engine, voice)

    def retime(self, state: PipelineState) -> TimingManifest:
        """Lay every segment out on the film timeline (script order, real durations)."""
        manifest = state.audio.manifest
        seg_by_line = {s.line_id: s for s in manifest.segments if s.kind == "dialogue"}
        plan = []
        for scene in state.script.scenes:
            lines = [(ln.line_id, seg_by_line[ln.line_id].duration_ms)
                     for ln in scene.dialogue if ln.line_id in seg_by_line]
            plan.append((scene.scene_id, lines, scene.duration_ms))
        scene_slots, line_slots, total_ms = build_timeline(plan)

        ordered: List[AudioSegment] = []
        for slot in line_slots:
            seg = seg_by_line[slot.line_id]
            seg.start_ms, seg.end_ms = slot.start_ms, slot.end_ms
            ordered.append(seg)
        manifest.segments = ordered + [s for s in manifest.segments if s.kind != "dialogue"]
        manifest.scenes = [SceneTiming(scene_id=s.scene_id, start_ms=s.start_ms, end_ms=s.end_ms)
                           for s in scene_slots]
        manifest.total_duration_ms = total_ms
        return manifest

    @staticmethod
    def _tone_offset_db(audio: AudioOutput, seg: AudioSegment) -> float:
        """How far this line is meant to sit from the other lines, in dB: a whispered tone (volume
        0.7) or a shout (1.3) was recorded that much off normal, and the mix, which brings every line
        to one loudness (DST-41), puts it back rather than erase it."""
        cfg = (audio.scene_voices.get(seg.scene_id) or {}).get(seg.character_id) or next(
            (c for c in audio.voice_configs if c.character_id == seg.character_id), None)
        volume = cfg.volume if cfg else 1.0
        return round(20.0 * math.log10(min(2.0, max(0.1, volume))), 2)

    def remix(self, state: PipelineState) -> str:
        """Rebuild the BGM bed (if enabled) and the master track from the timeline."""
        audio = state.audio
        audio.bgm_track = self._render_bgm(state) if audio.bgm_enabled else None
        audio.ambience_track = self._render_ambience(state)
        out = project_dir(state.project_id) / "audio" / "master.wav"
        placements = [{"path": s.file_path, "start_ms": s.start_ms, "offset_db": self._tone_offset_db(audio, s)}
                      for s in audio.manifest.segments if s.kind == "dialogue"]
        merge = self.tools.execute(
            "audio.merge",
            out_path=str(out),
            placements=placements,
            total_ms=audio.manifest.total_duration_ms,
            bgm=audio.bgm_track,
            bgm_volume=audio.music_volume,
            ambience=audio.ambience_track,
            ambience_volume=audio.ambience_volume,
            dialogue_volume=audio.voice_volume,
        )
        if not merge.success:
            raise RuntimeError(f"master mix failed: {merge.error}")
        audio.master_track = merge.data
        return merge.data

    # ---- voice configs ---------------------------------------------------

    def _build_voice_configs(self, script: ScriptOutput, default_engine: str) -> List[VoiceConfig]:
        out: List[VoiceConfig] = []
        languages = self.speaker_languages(script)
        for c in script.characters.characters:
            engine, voice_id, language = self.voice_plan(c, default_engine, languages.get(c.id))
            if engine != default_engine:
                log.info("%s speaks %s, which %s cannot voice: %s (%s) will",
                         c.name, languages[c.id], default_engine, engine, voice_id)
            out.append(VoiceConfig(
                character_id=c.id,
                engine=engine,
                language=language,
                voice_id=voice_id,
                tld=self._tld_for(c),
                rate=self._rate_for(c),
                tone=c.voice_style,
            ))
        return out

    @staticmethod
    def speaker_languages(script: ScriptOutput) -> Dict[str, Optional[str]]:
        """Each character's language, from the writing system of all their lines."""
        said: Dict[str, List[str]] = {}
        for scene in script.scenes:
            for line in scene.dialogue:
                said.setdefault(line.character_id, []).append(line.text)
        return {cid: spoken_language(" ".join(texts)) for cid, texts in said.items()}

    @classmethod
    def voice_plan(cls, c: Character, engine: str,
                   language: Optional[str] = None) -> Tuple[str, str, str]:
        """(engine, voice, gTTS language code) for a character speaking `language`.

        Kokoro reads only English, so a speaker of a language in SPEECH_VOICES
        moves to Edge TTS with a voice in that language; gTTS, Edge's fallback,
        gets the language code. Other engines keep their own voice and get the
        code too. English (or undetected) speakers are unchanged.
        """
        if language not in SPEECH_VOICES:
            return engine, cls.voice_for(c, engine), "en"
        code, male, female = SPEECH_VOICES[language]
        if engine in ("kokoro", "edge"):
            return "edge", female if c.voice_gender == "female" else male, code
        return engine, cls.voice_for(c, engine), code

    @staticmethod
    def normalise_voice_params(params: Dict[str, Any]) -> Dict[str, Any]:
        """An edit's voice parameters with the tone resolved to a known preset.

        An unknown tone raises rather than relabelling the voice and recording
        the lines exactly as before, which used to report success.
        """
        out = dict(params)
        tone = str(out.get("tone") or "").strip().lower()
        if tone:
            tone = TONE_ALIASES.get(tone, tone)
            if tone not in TONE_PRESETS:
                raise ValueError(f"unknown voice tone '{params['tone']}' "
                                 f"(known: {', '.join(TONE_PRESETS)})")
            out["tone"] = tone
        return out

    @classmethod
    def apply_voice_params(cls, cfg: VoiceConfig, params: Dict[str, Any],
                           character: Optional[Character] = None) -> None:
        """Apply an edit's voice parameters (tone / volume / voice) to a config."""
        params = cls.normalise_voice_params(params)
        tone = params.get("tone")
        if tone:
            preset = TONE_PRESETS.get(tone, {})
            cfg.tone = tone
            cfg.rate = preset.get("rate", cfg.rate)
            cfg.pitch = preset.get("pitch", cfg.pitch)
            cfg.volume = preset.get("volume", cfg.volume)
            if tone == "deep":
                cfg.tld = "co.uk"  # gTTS fallback has no pitch control
        volume = params.get("volume")
        if volume is not None:
            cfg.volume = max(0.0, min(2.0, round(cfg.volume * float(volume), 3)))
        voice = params.get("voice")
        if voice:
            if voice == "alternate":
                gender = character.voice_gender if character else "neutral"
                spoken = next((name for name, (code, *_rest) in SPEECH_VOICES.items()
                               if code == cfg.language), None)
                if spoken:
                    # The English pools would put the old bug back: an Urdu
                    # speaker given an English voice. Each language has one
                    # voice per gender here, so there is no alternate to offer.
                    raise ValueError(f"there is no other {spoken} voice to switch to")
                pools = KOKORO_POOL if cfg.engine == "kokoro" else VOICE_POOL
                pool = pools.get(gender, pools["neutral"])
                current = cfg.voice_id or ""
                idx = pool.index(current) if current in pool else -1
                cfg.voice_id = pool[(idx + 1) % len(pool)]
            else:
                cfg.voice_id = str(voice)

    @staticmethod
    def _tld_for(c: Character) -> str:
        # Different TLDs give noticeably different gTTS accents for the same lang.
        if c.voice_gender == "female":
            return "co.uk"
        if c.voice_age == "elderly":
            return "co.in"
        if c.role == "narrator":
            return "com"
        return "com.au"

    @classmethod
    def voice_for(cls, c: Character, engine: str) -> str:
        """Pick a voice that suits the character, in the engine's own naming."""
        if engine == "kokoro":
            if c.role == "narrator":
                return KOKORO_VOICES["narrator"]
            if c.voice_age in ("child", "elderly"):
                return KOKORO_VOICES[c.voice_age]
            return KOKORO_VOICES.get(c.voice_gender, KOKORO_VOICES["neutral"])
        return cls._edge_voice_for(c)

    @staticmethod
    def _edge_voice_for(c: Character) -> str:
        if c.role == "narrator":
            return "en-US-ChristopherNeural"
        if c.voice_age == "child" or "energetic" in c.voice_style.lower():
            return "en-US-AnaNeural"
        if c.voice_age == "elderly":
            return "en-GB-RyanNeural"
        if c.voice_gender == "female":
            return "en-US-AriaNeural"
        return "en-US-GuyNeural"

    @staticmethod
    def _rate_for(c: Character) -> int:
        if "whisper" in c.voice_style.lower():
            return 140
        if "energetic" in c.voice_style.lower() or c.voice_age == "child":
            return 200
        return 175

    # ---- BGM -------------------------------------------------------------

    def _render_bgm(self, state: PipelineState) -> Optional[str]:
        """The music under the film: per scene, the track the scene plays, cut from where the
        film's clock is (so a track carries on across scenes), joined end to end.

        A library track is a real bed (shared/music.py); only if the library is missing or a
        track cannot be read does it fall back to the old generated tones, so a bare checkout
        still works.
        """
        audio = state.audio
        bgm_dir = project_dir(state.project_id) / "audio" / "bgm"
        bgm_dir.mkdir(exist_ok=True, parents=True)
        scenes = {s.scene_id: s for s in state.script.scenes}
        plan = []
        for i, timing in enumerate(audio.manifest.scenes):
            scene = scenes.get(timing.scene_id)
            choice = music.choice_for_scene(audio, scene, i) if scene else None
            plan.append((timing, scene, choice))
        parts: List[Path] = []
        for i, (timing, scene, choice) in enumerate(plan):
            out_file = bgm_dir / f"{timing.scene_id}_bgm.wav"
            try:
                if choice is None:
                    music.silence(timing.duration_ms, out_file)
                else:
                    before = plan[i - 1][2] if i else None
                    after = plan[i + 1][2] if i + 1 < len(plan) else None
                    length = music.track_seconds(choice, audio.uploads)
                    offset = int(timing.start_ms % int(length * 1000)) if length else 0
                    music.cut_bed(music.track_path(choice, audio.uploads), offset, timing.duration_ms,
                                  out_file, fade_in_ms=350 if choice != before else 0,
                                  fade_out_ms=350 if choice != after else 0)
                parts.append(out_file)
            except (music.MusicError, subprocess.CalledProcessError) as exc:
                log.warning("music failed for %s (%s); using generated tones", timing.scene_id, exc)
                res = self.tools.execute("audio.bgm", mood=scene.music_mood if scene else "ambient",
                                         duration_ms=timing.duration_ms, out_path=str(out_file))
                if res.success:
                    parts.append(Path(res.data))
        if not parts:
            return None
        return str(music.join(parts, bgm_dir / "bgm_full.wav"))

    def _render_ambience(self, state: PipelineState) -> Optional[str]:
        """The ambience bed (rain, wind, ...) per scene, or None when no scene has any."""
        audio = state.audio
        wanted = []
        for timing in audio.manifest.scenes:
            kind = audio.ambience.get(timing.scene_id, audio.ambience.get("*"))
            wanted.append(None if kind in (None, "none") else kind)
        if not any(wanted):
            return None
        amb_dir = project_dir(state.project_id) / "audio" / "ambience"
        amb_dir.mkdir(exist_ok=True, parents=True)
        parts: List[Path] = []
        for i, (timing, kind) in enumerate(zip(audio.manifest.scenes, wanted)):
            out_file = amb_dir / f"{timing.scene_id}_amb.wav"
            if kind is None:
                music.silence(timing.duration_ms, out_file)
            else:
                # Fade only where the ambience begins or ends; between two scenes of the same
                # kind it carries straight on, with no fade at all (a fade out and a fade in left a
                # hole at every cut, DST-41). Where the kind changes, each bed fades at its own end.
                starts = i == 0 or wanted[i - 1] != kind
                ends = i + 1 == len(wanted) or wanted[i + 1] != kind
                music.ambience_bed(kind, timing.duration_ms, out_file,
                                   fade_in_ms=300 if starts else 0, fade_out_ms=300 if ends else 0)
            parts.append(out_file)
        return str(music.join(parts, amb_dir / "ambience_full.wav"))

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _probe_duration_ms(path: Path) -> Optional[int]:
        if not path.exists():
            return None
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, check=True,
            )
            return int(float(r.stdout.strip()) * 1000)
        except Exception:  # noqa: BLE001
            return None

    # ---- serialization ---------------------------------------------------

    def _serialize(self, project_id: str, output: AudioOutput) -> List[str]:
        proj = project_dir(project_id)
        manifest_path = proj / "timing_manifest.json"
        write_json(manifest_path, output.manifest.model_dump(mode="json"))
        audio_summary = proj / "audio_summary.json"
        write_json(audio_summary, {
            "project_id": project_id,
            "phase": PHASE_AUDIO,
            "status": "complete",
            "voice_configs": [v.model_dump(mode="json") for v in output.voice_configs],
            "scene_voices": {scene: {cid: v.model_dump(mode="json") for cid, v in voices.items()}
                             for scene, voices in output.scene_voices.items()},
            "segment_count": len(output.manifest.segments),
            "total_duration_ms": output.manifest.total_duration_ms,
            "bgm_track": output.bgm_track,
            "master_track": output.master_track,
        })
        artifacts = [str(manifest_path), str(audio_summary)]
        if output.bgm_track:
            artifacts.append(output.bgm_track)
        if output.master_track:
            artifacts.append(output.master_track)
        artifacts.extend(s.file_path for s in output.manifest.segments)
        return artifacts

    def serialize(self, state: PipelineState) -> List[str]:
        """Re-write the audio artifacts after an edit and refresh artifact paths."""
        state.phase2.artifact_paths = self._serialize(state.project_id, state.audio)
        return state.phase2.artifact_paths
