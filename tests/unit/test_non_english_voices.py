"""DST-17: dialogue Kokoro cannot read goes to a voice that speaks its language,
and a film far from its target length is flagged rather than reported as a
plain success.

Found in the DST-10 baseline: dst-05's Urdu lines were read by Kokoro as English
and the 30 s film came out 150.8 s long, reported as a success.
"""
from __future__ import annotations

import pytest

from agents.audio_agent.agent import AudioAgent
from agents.orchestrator.workflow import PipelineOrchestrator
from mcp.tools.audio_tools.tts_tool import TtsTool
from shared.languages import spoken_language
from shared.schemas.story import Character

# Urdu, with letters Arabic does not use (ہ, ے), as the story model wrote dst-05.
URDU = "ہم کل صبح سمندر پر جائیں گے، طوفان سے پہلے"


@pytest.mark.parametrize("text,expected", [
    (URDU, "Urdu"),
    ("مرحبا بك في البيت", "Arabic"),
    ("नमस्ते दोस्त, कैसे हो", "Hindi"),
    ("こんにちは、世界", "Japanese"),
    ("你好，世界", "Chinese"),
    ("안녕하세요 친구", "Korean"),
    ("Привет, друг", "Russian"),
    ("Grandpa, the storm is coming.", None),
    ("Abuelo, la radio vuelve a sonar.", None),    # Latin script stays with Kokoro
    ("Dadi, patang ooper ja rahi hai!", None),      # Roman Urdu too
    (f"{URDU} Ali", "Urdu"),                         # an English name inside Urdu
    ("", None),
    ("... 42 !", None),
])
def test_the_writing_system_names_the_language(text, expected):
    assert spoken_language(text) == expected


def _character(gender="male", role="protagonist"):
    return Character(id="char_a", name="Ali", role=role, description="a fisherman",
                     visual_description="an old fisherman in a blue kurta",
                     voice_gender=gender)


@pytest.mark.parametrize("engine,gender,expected", [
    ("kokoro", "male", ("edge", "ur-PK-AsadNeural", "ur")),
    ("kokoro", "female", ("edge", "ur-PK-UzmaNeural", "ur")),
    ("edge", "male", ("edge", "ur-PK-AsadNeural", "ur")),
])
def test_a_speaker_kokoro_cannot_voice_moves_to_edge(engine, gender, expected):
    assert AudioAgent.voice_plan(_character(gender), engine, "Urdu") == expected


def test_other_engines_keep_their_voice_and_get_the_language_code():
    hero = _character()
    assert AudioAgent.voice_plan(hero, "gtts", "Urdu") == (
        "gtts", AudioAgent.voice_for(hero, "gtts"), "ur")


@pytest.mark.parametrize("language", [None, "English", "Spanish"])
def test_english_and_undetected_speakers_are_unchanged(language):
    hero = _character()
    assert AudioAgent.voice_plan(hero, "kokoro", language) == (
        "kokoro", AudioAgent.voice_for(hero, "kokoro"), "en")


def _script_with_an_urdu_speaker():
    from agents.story_agent.planner import template_script
    script = template_script("t_urdu", "A fisherman and his grandson", target_duration_s=20,
                             scene_count=2)
    speaker = script.scenes[0].dialogue[0].character_id
    for scene in script.scenes:
        for line in scene.dialogue:
            if line.character_id == speaker:
                line.text = URDU
    return script, speaker


@pytest.fixture
def recorded_voices(monkeypatch):
    """Kokoro and Edge write silence offline and record who was asked for what."""
    calls = []

    def fake_kokoro(self, text, out, voice="", rate=175):
        calls.append(("kokoro", text, voice))
        return self._silent_wav(out, duration_s=0.5)

    def fake_edge(self, text, out, voice="", rate=175, pitch=0, volume=1.0):
        calls.append(("edge", text, voice))
        return self._silent_wav(out, duration_s=0.5)

    monkeypatch.setattr(TtsTool, "_kokoro", fake_kokoro)
    monkeypatch.setattr(TtsTool, "_edge_tts", fake_edge)
    return calls


def test_urdu_lines_are_voiced_by_edge_and_english_lines_by_kokoro(isolated_dirs,
                                                                   recorded_voices):
    from shared.schemas.pipeline import PipelineState

    script, speaker = _script_with_an_urdu_speaker()
    state = PipelineState(project_id="t_urdu", user_prompt="A fisherman and his grandson",
                          script=script)
    AudioAgent().run(state, with_bgm=False, tts_engine="kokoro")

    for engine, text, voice in recorded_voices:
        if text == URDU:
            assert (engine, voice[:6]) == ("edge", "ur-PK-")
        else:
            assert engine == "kokoro" and not voice.startswith("ur-")
    assert {text for _e, text, _v in recorded_voices} >= {URDU}
    # Who spoke each line is kept with the film.
    for seg in state.audio.manifest.segments:
        if seg.kind != "dialogue":
            continue
        if seg.character_id == speaker:
            assert seg.engine == "edge" and seg.voice.startswith("ur-PK-")
        else:
            assert seg.engine == "kokoro"
    urdu_cfg = next(v for v in state.audio.voice_configs if v.character_id == speaker)
    assert (urdu_cfg.engine, urdu_cfg.language) == ("edge", "ur")


def test_when_edge_is_down_gtts_speaks_the_line_in_urdu(isolated_dirs, monkeypatch):
    seen = {}

    def edge_down(self, *a, **k):
        raise RuntimeError("no network")

    def fake_gtts(self, text, out, language="en", tld="com"):
        seen[text] = language
        return self._silent_wav(out, duration_s=0.5)

    monkeypatch.setattr(TtsTool, "_edge_tts", edge_down)
    monkeypatch.setattr(TtsTool, "_gtts", fake_gtts)
    res = TtsTool().run(text=URDU, out_path=str(isolated_dirs / "line.mp3"),
                        engine="edge", voice="ur-PK-AsadNeural", language="ur")
    assert res.metadata["engine"] == "gtts" and seen[URDU] == "ur"


def test_the_storyboard_names_the_voice_that_will_speak(isolated_dirs):
    from agents.orchestrator.storyboard_view import cast, scene_cards

    script, speaker = _script_with_an_urdu_speaker()
    name = next(c.name for c in script.characters.characters if c.id == speaker)
    card_voices = {ln["character"]: ln["voice"] for card in scene_cards(script, "kokoro")
                   for ln in card["lines"]}
    assert card_voices[name] in ("Asad", "Uzma") or "ur-PK" in card_voices[name]
    cast_voice = next(c["voice"] for c in cast(script, "kokoro") if c["character_id"] == speaker)
    assert cast_voice == card_voices[name]


def test_switching_an_urdu_speaker_to_another_voice_fails_loudly():
    """The English voice pools would quietly put the bug back."""
    from shared.schemas.audio import VoiceConfig
    cfg = VoiceConfig(character_id="char_a", engine="edge", voice_id="ur-PK-AsadNeural",
                      language="ur")
    with pytest.raises(ValueError, match="no other Urdu voice"):
        AudioAgent.apply_voice_params(cfg, {"voice": "alternate"}, _character())
    assert cfg.voice_id == "ur-PK-AsadNeural"


# ---- the length check -------------------------------------------------------------

def _film_of_length(small_project, film_s: float):
    state, _ = small_project(duration_s=30, scenes=2, with_subtitles=False)
    state.video.duration_ms = int(film_s * 1000)
    return state


@pytest.mark.parametrize("film_s", [150.8, 14.0])      # dst-05's length; far too short
def test_a_film_far_off_its_target_is_flagged(small_project, film_s):
    note, payload = PipelineOrchestrator._completion_notes(_film_of_length(small_project, film_s))
    assert payload["length_warning"] == {"film_s": film_s, "target_s": 30, "tolerance": 0.5}
    assert f"{film_s:.1f} s for a 30 s target" in note


@pytest.mark.parametrize("film_s", [17.4, 30.0, 44.0])  # DST-10's worst normal film was 42% short
def test_a_film_within_the_tolerance_is_not_flagged(small_project, film_s):
    note, payload = PipelineOrchestrator._completion_notes(_film_of_length(small_project, film_s))
    assert "length_warning" not in payload and note == ""


def test_the_tolerance_comes_from_the_environment(small_project, monkeypatch):
    state = _film_of_length(small_project, 36.0)            # 20% long
    monkeypatch.setenv("FILM_LENGTH_TOLERANCE", "0.1")
    assert "length_warning" in PipelineOrchestrator._completion_notes(state)[1]
    monkeypatch.setenv("FILM_LENGTH_TOLERANCE", "nonsense")  # back to the default 0.5
    assert "length_warning" not in PipelineOrchestrator._completion_notes(state)[1]


def test_the_flag_reaches_the_completion_event_of_a_real_run(isolated_dirs, monkeypatch):
    from state_manager.state_manager import StateManager
    from state_manager.storage import SqliteStorage
    from tests.conftest import silence_tts

    # A tolerance so tight that any real film is outside it, to see the wiring.
    monkeypatch.setenv("FILM_LENGTH_TOLERANCE", "0.0001")
    orch = PipelineOrchestrator(state_manager=StateManager(SqliteStorage(isolated_dirs / "db")))
    silence_tts(orch.audio.tools)
    events = []
    state = orch.run_full("A kite flies over a quiet town", on_event=events.append,
                          target_duration_s=20, scene_count=2, with_bgm=False,
                          with_subtitles=False, width=320, height=180, fps=12)
    done = events[-1]
    assert (done.phase, done.status) == ("complete", "complete")   # still a finished film
    assert done.payload["length_warning"]["target_s"] == 20
    assert done.payload["length_warning"]["film_s"] == round(state.video.duration_ms / 1000, 1)
    assert "length off target" in done.message
