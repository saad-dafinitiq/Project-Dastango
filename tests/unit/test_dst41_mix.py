"""DST-41 (QA finding H1): the dialogue is the anchor of the mix.

Measured on real films before this change: speech was only 2 to 9 dB over the music and ambience
while a line played, 1.9 dB with rain, and the bed came back between lines at about the level of the
voices. Editors keep a bed 18 to 25 dB under speech (WCAG 1.4.7 asks for 20), so these tests hold the
mix to that, with a 300 Hz tone for the voice and a 900 Hz tone and rain for the bed, which lets each
be measured alone.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mcp.tool_executor import ToolExecutor
from mcp.tools.audio_tools import audio_merger as am
from shared import music

VOICE_HZ, MUSIC_HZ = 300, 900


def _tone(path: Path, hz: int, seconds: float, volume: float) -> str:
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}",
                    "-af", f"volume={volume}", "-ar", "22050", "-ac", "1", str(path)],
                   check=True, capture_output=True)
    return str(path)


def _mean_db(path: str, start: float, end: float, filters: str = "") -> float:
    """Mean level of a slice. The slice is cut inside the filter chain: an output-side `-ss/-to` is
    applied *after* the filters on recent ffmpeg builds, so volumedetect then averages the whole file
    and every window reads as a drifting running mean."""
    chain = f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS," + (filters + "," if filters else "") + "volumedetect"
    proc = subprocess.run(["ffmpeg", "-i", path, "-af", chain, "-f", "null", "-"],
                          capture_output=True, text=True)
    for line in proc.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    raise AssertionError("no mean_volume")


def _voice_db(path, a, b):          # the voice alone: a band around 300 Hz
    return _mean_db(path, a, b, f"bandpass=f={VOICE_HZ}:width_type=h:w=120")


def _bed_db(path, a, b):            # everything but the voice: a notch at 300 Hz
    return _mean_db(path, a, b, f"bandreject=f={VOICE_HZ}:width_type=h:w=120")


def _merge(tmp: Path, name: str, lines, total_ms: int, **kw) -> str:
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / f"{name}.wav"
    res = ToolExecutor().execute("audio.merge", out_path=str(out), total_ms=total_ms, placements=lines, **kw)
    assert res.success, res.error
    return res.data


def _cut(src: str, a: float, b: float, dst: Path) -> str:
    subprocess.run(["ffmpeg", "-y", "-i", src, "-ss", str(a), "-to", str(b), str(dst)], check=True, capture_output=True)
    return str(dst)


# ---- the voices: one loudness, a tone's own offset kept --------------------------------------

def test_every_line_is_brought_to_one_loudness(tmp_path):
    loud = _tone(tmp_path / "loud.wav", VOICE_HZ, 2.0, 4.0)
    quiet = _tone(tmp_path / "quiet.wav", VOICE_HZ, 2.0, 1.0)            # 12 dB quieter
    mix = _merge(tmp_path, "m", [{"path": loud, "start_ms": 0}, {"path": quiet, "start_ms": 3000}],
                 6000, loudness=False, duck=False)
    a = am.measure_lufs(Path(_cut(mix, 0.2, 1.8, tmp_path / "a.wav")))
    b = am.measure_lufs(Path(_cut(mix, 3.2, 4.8, tmp_path / "b.wav")))
    assert abs(a - b) < 0.8, f"lines were {a:.1f} and {b:.1f} LUFS"
    assert abs(a - am.LINE_LUFS) < 1.5


def test_a_line_far_too_quiet_is_lifted_by_no_more_than_12_db(tmp_path):
    normal = _tone(tmp_path / "n.wav", VOICE_HZ, 2.0, 4.0)
    faint = _tone(tmp_path / "f.wav", VOICE_HZ, 2.0, 0.08)               # 34 dB under: a fault, not a voice
    mix = _merge(tmp_path, "m", [{"path": normal, "start_ms": 0}, {"path": faint, "start_ms": 3000}],
                 6000, loudness=False, duck=False)
    a = am.measure_lufs(Path(_cut(mix, 0.2, 1.8, tmp_path / "a.wav")))
    b = am.measure_lufs(Path(_cut(mix, 3.2, 4.8, tmp_path / "b.wav")))
    assert a - b > 8, f"a {a:.1f}, faint {b:.1f}: it was levelled all the way"


def test_a_whisper_stays_that_far_under_the_other_lines(tmp_path):
    first = _tone(tmp_path / "a.wav", VOICE_HZ, 2.0, 4.0)
    second = _tone(tmp_path / "b.wav", VOICE_HZ, 2.0, 4.0)
    mix = _merge(tmp_path, "m", [{"path": first, "start_ms": 0, "offset_db": 0.0},
                                 {"path": second, "start_ms": 3000, "offset_db": -3.1}],
                 6000, loudness=False, duck=False)
    a = am.measure_lufs(Path(_cut(mix, 0.2, 1.8, tmp_path / "a2.wav")))
    b = am.measure_lufs(Path(_cut(mix, 3.2, 4.8, tmp_path / "b2.wav")))
    assert 2.2 < a - b < 4.0, f"a {a:.1f} LUFS, whisper {b:.1f} LUFS"


def test_the_audio_agent_reads_a_lines_tone_offset_from_its_voice():
    from agents.audio_agent.agent import AudioAgent
    from shared.schemas.audio import AudioOutput, AudioSegment, TimingManifest, VoiceConfig

    def seg(scene, who):
        return AudioSegment(segment_id=f"{scene}_{who}", scene_id=scene, line_id="l1", character_id=who,
                            file_path="x.mp3", kind="dialogue", start_ms=0, end_ms=1000, duration_ms=1000, text="hi")

    audio = AudioOutput(
        voice_configs=[VoiceConfig(character_id="a", engine="edge", voice_id="v", volume=1.0),
                       VoiceConfig(character_id="b", engine="edge", voice_id="v", volume=0.7)],
        manifest=TimingManifest(project_id="p", total_duration_ms=1000))
    audio.scene_voices["scene_2"] = {"a": VoiceConfig(character_id="a", engine="edge", voice_id="v", volume=1.3)}
    assert AudioAgent._tone_offset_db(audio, seg("scene_1", "a")) == 0.0
    assert AudioAgent._tone_offset_db(audio, seg("scene_1", "b")) == pytest.approx(-3.1, abs=0.05)
    assert AudioAgent._tone_offset_db(audio, seg("scene_2", "a")) == pytest.approx(2.28, abs=0.05)   # the scene's own voice


# ---- the bed: far enough under the voice, however loud its file is ---------------------------

def _film(tmp: Path, name: str, rain: bool, music_amp: float = 4.0, **kw) -> str:
    tmp.mkdir(parents=True, exist_ok=True)
    voice = _tone(tmp / f"{name}_v.wav", VOICE_HZ, 2.0, 4.0)
    bed = _tone(tmp / f"{name}_m.wav", MUSIC_HZ, 9.0, music_amp)
    extra = {}
    if rain:
        extra = {"ambience": str(music.ambience_bed("rain", 9000, tmp / f"{name}_r.wav")), "ambience_volume": 0.22}
    return _merge(tmp, name, [{"path": voice, "start_ms": 500}], 9000, bgm=bed, bgm_volume=0.32, **extra, **kw)


def _balance(tmp: Path, rain: bool):
    """(speech, bed under the line, bed between lines) in dB, before the master.

    The voice and the bed are measured from two mixes that differ only in the voice (a tone, or the
    same length of silence). Ducking follows the line's time, not its sound, so the silent-voice mix
    shows exactly what the bed does under the line, with no voice leaking into the measurement."""
    tmp.mkdir(parents=True, exist_ok=True)
    voice = _tone(tmp / "v.wav", VOICE_HZ, 2.0, 4.0)
    silent = tmp / "s.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "2", str(silent)],
                   check=True, capture_output=True)
    bed = _tone(tmp / "m.wav", MUSIC_HZ, 9.0, 4.0)
    extra = {}
    if rain:
        extra = {"ambience": str(music.ambience_bed("rain", 9000, tmp / "r.wav")), "ambience_volume": 0.22}
    # The voice goes through the same mix as the bed (with a silent bed), so both are measured in the
    # same format: a mix with no bed at all is written differently (mono, not stereo).
    quiet_bed = tmp / "quiet.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "9", str(quiet_bed)],
                   check=True, capture_output=True)
    only_voice = _merge(tmp, "voice", [{"path": voice, "start_ms": 500}], 9000, bgm=str(quiet_bed), loudness=False)
    only_bed = _merge(tmp, "bed", [{"path": str(silent), "start_ms": 500}], 9000, bgm=bed, bgm_volume=0.32,
                      loudness=False, **extra)
    return _mean_db(only_voice, 0.7, 2.3), _mean_db(only_bed, 0.7, 2.3), _mean_db(only_bed, 6.0, 8.5)


@pytest.mark.parametrize("rain", [False, True])
def test_speech_is_at_least_18_db_over_the_bed_while_a_line_plays(tmp_path, rain):
    speech, under, gap = _balance(tmp_path, rain)
    assert speech - under >= 18.0, f"speech only {speech - under:.1f} dB over the bed (rain={rain})"
    assert speech - under <= 28.0, f"the bed is {speech - under:.1f} dB under the line: it is buried, not ducked"


@pytest.mark.parametrize("rain", [False, True])
def test_between_lines_the_bed_sits_about_15_db_under_the_voice(tmp_path, rain):
    speech, under, gap = _balance(tmp_path, rain)
    assert 12.0 <= speech - gap <= 18.0, f"{speech - gap:.1f} dB (rain={rain})"


def test_the_duck_is_a_gentle_dip_and_the_bed_climbs_back_softly(tmp_path):
    speech, under, gap = _balance(tmp_path, rain=False)
    assert 6.0 <= gap - under <= 8.5, f"the bed dipped {gap - under:.1f} dB under the line"
    only_bed = tmp_path / "bed.wav"
    just_after = _mean_db(str(only_bed), 2.6, 2.9)        # the line ended at 2.5 s
    later = _mean_db(str(only_bed), 3.6, 4.0)
    assert under + 1.0 < just_after < later + 0.3 <= gap + 0.6, (under, just_after, later, gap)


def test_lines_close_together_make_one_dip_not_a_flutter():
    spans = am.duck_spans([(0.5, 2.0), (2.4, 3.5), (6.0, 7.0)])
    assert spans == [(0.5, 3.5), (6.0, 7.0)]
    assert am.duck_spans([]) == []


def test_the_bed_does_not_depend_on_how_loud_its_file_is(tmp_path):
    loud = _bed_db(_film(tmp_path / "x", "loud", False, music_amp=4.0, duck=False, loudness=False), 4.0, 8.0)
    faint = _bed_db(_film(tmp_path / "y", "faint", False, music_amp=0.4, duck=False, loudness=False), 4.0, 8.0)
    assert abs(loud - faint) < 0.7, f"a 20 dB louder file made the bed {loud - faint:.1f} dB louder"


def test_the_music_slider_still_moves_the_bed(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    voice = _tone(tmp_path / "v.wav", VOICE_HZ, 2.0, 4.0)
    bed = _tone(tmp_path / "m.wav", MUSIC_HZ, 6.0, 3.0)
    levels = {}
    for pct, vol in (("100", 0.32), ("200", 0.64), ("50", 0.16)):
        mix = _merge(tmp_path, f"s{pct}", [{"path": voice, "start_ms": 0}], 6000, bgm=bed, bgm_volume=vol,
                     duck=False, loudness=False)
        levels[pct] = _bed_db(mix, 3.0, 5.5)
    assert levels["200"] - levels["100"] == pytest.approx(6.0, abs=0.7)
    assert levels["100"] - levels["50"] == pytest.approx(6.0, abs=0.7)


# ---- the master lands on the target ----------------------------------------------------------

def test_the_finished_film_is_at_the_loudness_target(tmp_path):
    mix = _film(tmp_path, "m", rain=True)
    heard = am.measure_lufs(Path(mix))
    assert abs(heard - am.MASTER_LUFS) < 0.3, f"{heard:.2f} LUFS"


def test_a_film_with_only_voices_lands_on_the_target_too(tmp_path):
    voice = _tone(tmp_path / "v.wav", VOICE_HZ, 3.0, 0.4)
    mix = _merge(tmp_path, "m", [{"path": voice, "start_ms": 0}], 3000)
    assert abs(am.measure_lufs(Path(mix)) - am.MASTER_LUFS) < 0.5


def test_the_mix_is_exactly_as_long_as_the_timeline(tmp_path):
    mix = _film(tmp_path, "m", rain=True)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=noprint_wrappers=1:nokey=1", mix], capture_output=True, text=True, check=True)
    assert abs(float(out.stdout) - 9.0) < 0.06


def test_a_silent_film_does_not_break_the_master(tmp_path):
    silent = tmp_path / "s.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "2", str(silent)],
                   check=True, capture_output=True)
    mix = _merge(tmp_path, "m", [{"path": str(silent), "start_ms": 0}], 2000)
    assert Path(mix).exists()


def _first_sound_s(path: str, threshold: float = 0.02) -> float:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "22050", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    import numpy as np
    x = np.abs(np.frombuffer(raw, dtype=np.float32))
    return float(np.argmax(x > threshold)) / 22050.0


@pytest.mark.parametrize("with_bed", [False, True])
def test_the_master_does_not_shift_the_voices_in_time(tmp_path, with_bed):
    """The limiter in the master looks 5 ms ahead and delayed the whole mix by that much (109 samples
    late against the picture, found by comparing a real film's master with the voices it was made from)."""
    voice = _tone(tmp_path / "v.wav", VOICE_HZ, 1.0, 4.0)
    extra = {}
    if with_bed:
        extra = {"bgm": _tone(tmp_path / "m.wav", MUSIC_HZ, 6.0, 0.4), "bgm_volume": 0.32}
    mix = _merge(tmp_path, "m", [{"path": voice, "start_ms": 2000}], 6000, **extra)
    assert abs(_first_sound_s(mix, 0.2 if with_bed else 0.02) - 2.0) < 0.002
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=noprint_wrappers=1:nokey=1", mix], capture_output=True, text=True, check=True)
    assert abs(float(out.stdout) - 6.0) < 0.01
