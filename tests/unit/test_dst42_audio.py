"""DST-42: the audio chain above 22.05 kHz mono, an ambience that sits clear of speech.

Found by the QA of 9 Nov 2026: voices, beds and the finished mix were 22.05 kHz mono, an uploaded stereo
track was folded to mono at 96 kbps, rain and night were about 4 LU louder than wind and city, and rain and
night had their energy in the 2 to 5 kHz band where consonants live.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from mcp.tools.audio_tools.audio_merger import AudioMergerTool, measure_lufs
from shared import music


def _stream(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=sample_rate,channels,bit_rate", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


def _channels(path: Path) -> np.ndarray:
    """The file as (samples, channels), float."""
    n = int(_stream(path)["channels"])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, n)


def _share_2_to_5_khz(path: Path) -> float:
    x = _channels(path).mean(axis=1)
    f = np.fft.rfftfreq(len(x), 1 / int(_stream(path)["sample_rate"]))
    power = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    return float(power[(f >= 2000) & (f <= 5000)].sum() / power.sum())


# ---- the chain's rate and channels --------------------------------------------------------------------

def test_the_chain_is_cd_rate_and_stereo():
    assert music.SAMPLE_RATE == 44100 and music.CHANNELS == "stereo"


def test_a_music_bed_is_44k_stereo(tmp_path):
    bed = music.cut_bed(music.library()["ambient-1"].path, 2_000, 2_000, tmp_path / "m.wav")
    s = _stream(bed)
    assert (int(s["sample_rate"]), int(s["channels"])) == (44100, 2)
    left, right = _channels(bed).T
    assert np.allclose(left, right, atol=1e-4)              # a mono track is the same in both ears


def test_the_finished_mix_is_44k_stereo_and_exactly_as_long_as_the_timeline(tmp_path):
    voice = tmp_path / "v.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=220:d=2", "-ar", "24000", "-ac", "1", str(voice)],
                   check=True, capture_output=True)
    rain = music.ambience_bed("rain", 6_000, tmp_path / "rain.wav")
    bed = music.cut_bed(music.library()["tense-1"].path, 0, 6_000, tmp_path / "bed.wav")
    out = tmp_path / "master.wav"
    done = AudioMergerTool().run(out_path=str(out), placements=[{"path": str(voice), "start_ms": 1_000}],
                                 total_ms=6_000, bgm=str(bed), ambience=str(rain))
    assert done.success, done.error
    s = _stream(out)
    assert (int(s["sample_rate"]), int(s["channels"])) == (44100, 2)
    assert abs(len(_channels(out)) / 44100 - 6.0) < 0.002
    assert abs(measure_lufs(out) - (-16.0)) < 0.3


def test_a_voice_only_film_is_44k_stereo_too(tmp_path):
    voice = tmp_path / "v.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=220:d=2", "-ar", "24000", "-ac", "1", str(voice)],
                   check=True, capture_output=True)
    out = tmp_path / "master.wav"
    assert AudioMergerTool().run(out_path=str(out), placements=[{"path": str(voice), "start_ms": 0}], total_ms=3_000).success
    assert (int(_stream(out)["sample_rate"]), int(_stream(out)["channels"])) == (44100, 2)


def test_a_rendered_film_carries_cd_rate_stereo_audio(small_project):
    state, _sm = small_project(project_id="t_d42_film", with_bgm=True)
    s = _stream(Path(state.video.final_video_path))
    assert int(s["sample_rate"]) >= 44100 and int(s["channels"]) == 2


# ---- uploads ------------------------------------------------------------------------------------------

def _tone(path: Path, channels: int, seconds: float = 4.0) -> Path:
    # a different tone in each ear, so a fold to mono would show
    src = "sine=f=300:d={d}" if channels == 1 else "amerge=inputs=2"
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=f=300:d={seconds}"]
    if channels == 2:
        cmd += ["-f", "lavfi", "-i", f"sine=f=600:d={seconds}", "-filter_complex", "[0:a][1:a]amerge=inputs=2"]
    subprocess.run([*cmd, "-ar", "48000", str(path)], check=True, capture_output=True)
    return path


def test_a_stereo_upload_stays_stereo_at_a_good_bitrate(tmp_path):
    src = _tone(tmp_path / "in.wav", 2)
    record = music.store_upload(src, "in.wav", tmp_path / "up")
    s = _stream(Path(record["path"]))
    assert int(s["channels"]) == 2 and int(s["sample_rate"]) == 44100
    assert int(s["bit_rate"]) >= 160_000
    left, right = _channels(Path(record["path"])).T
    assert abs(np.corrcoef(left, right)[0, 1]) < 0.5            # still two different sounds, not folded


def test_a_mono_upload_stays_mono_at_44k(tmp_path):
    record = music.store_upload(_tone(tmp_path / "in.wav", 1), "in.wav", tmp_path / "up")
    s = _stream(Path(record["path"]))
    assert int(s["channels"]) == 1 and int(s["sample_rate"]) == 44100


def test_a_stereo_upload_plays_in_stereo_under_the_voice(tmp_path):
    record = music.store_upload(_tone(tmp_path / "in.wav", 2), "in.wav", tmp_path / "up")
    bed = music.cut_bed(Path(record["path"]), 0, 2_000, tmp_path / "bed.wav")
    left, right = _channels(bed).T
    assert abs(np.corrcoef(left, right)[0, 1]) < 0.5


# ---- the ambience --------------------------------------------------------------------------------------

@pytest.mark.parametrize("seconds", [4, 12])
def test_the_five_ambience_kinds_are_within_a_loudness_unit_and_a_half(tmp_path, seconds):
    heard = {k: measure_lufs(music.ambience_bed(k, seconds * 1000, tmp_path / f"{k}{seconds}.wav"))
             for k in music.AMBIENCE}
    assert max(heard.values()) - min(heard.values()) <= 1.5, heard
    # and they sit where the table says, not just near each other (a bed of a few seconds sees only part of
    # the slow swell of wind or sea, so a short one may be a little off)
    assert all(abs(v - music.AMBIENCE_LUFS) < 1.5 for v in heard.values()), heard


def test_no_ambience_keeps_its_energy_in_the_speech_band(tmp_path):
    """Rain had 36% and night 63% of their energy between 2 and 5 kHz, where consonants are, so under a line
    they masked it. Now each is under 8%."""
    share = {k: _share_2_to_5_khz(music.ambience_bed(k, 10_000, tmp_path / f"{k}.wav")) for k in music.AMBIENCE}
    assert all(v < 0.08 for v in share.values()), {k: round(v, 3) for k, v in share.items()}


def test_rain_and_night_use_the_band_the_rate_opened_up(tmp_path):
    """The point of 44.1 kHz for the beds: energy above 11 kHz, which 22.05 kHz could not hold."""
    def above(kind):
        x = _channels(music.ambience_bed(kind, 8_000, tmp_path / f"{kind}.wav")).mean(axis=1)
        f = np.fft.rfftfreq(len(x), 1 / 44100)
        p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        return float(p[f > 11_000].sum() / p.sum())
    assert above("rain") > 0.02 and above("night") > 0.1


def test_an_ambience_bed_is_wide_not_a_line_down_the_middle(tmp_path):
    for kind in music.AMBIENCE:
        left, right = _channels(music.ambience_bed(kind, 6_000, tmp_path / f"{kind}.wav")).T
        assert abs(np.corrcoef(left, right)[0, 1]) < 0.5, kind


def test_a_bed_is_the_same_every_time_it_is_drawn(tmp_path):
    a = _channels(music.ambience_bed("rain", 2_000, tmp_path / "a.wav"))
    b = _channels(music.ambience_bed("rain", 2_000, tmp_path / "b.wav"))
    assert np.array_equal(a, b)


def test_scenes_of_one_ambience_join_into_one_continuous_sound(tmp_path):
    """The bed for a scene is taken from the film's own clock, so two scenes meet where the swell is, not at
    the start of a new one: the two halves are the whole."""
    whole = _channels(music.ambience_bed("sea", 8_000, tmp_path / "w.wav", fade_in_ms=0, fade_out_ms=0))
    first = music.ambience_bed("sea", 4_000, tmp_path / "a.wav", fade_in_ms=0, fade_out_ms=0)
    second = music.ambience_bed("sea", 4_000, tmp_path / "b.wav", fade_in_ms=0, fade_out_ms=0, offset_ms=4_000)
    joined = _channels(music.join([first, second], tmp_path / "j.wav"))
    n = min(len(whole), len(joined))
    assert np.abs(whole[:n] - joined[:n]).max() < 0.02


def test_the_fallback_tones_match_the_chain_so_the_beds_can_still_be_joined(tmp_path):
    from mcp.tools.audio_tools.bgm_tool import BgmTool

    res = BgmTool().run("tense", 2_000, str(tmp_path / "t.wav"))
    s = _stream(Path(res.data))
    assert (int(s["sample_rate"]), int(s["channels"])) == (44100, 2)
    tone = music.cut_bed(music.library()["tense-1"].path, 0, 2_000, tmp_path / "c.wav")
    music.join([Path(res.data), tone], tmp_path / "j.wav")      # same format: the concat demuxer joins them
    assert (int(_stream(tmp_path / "j.wav")["sample_rate"]), int(_stream(tmp_path / "j.wav")["channels"])) == (44100, 2)


def test_putting_the_voice_in_both_ears_does_not_make_it_quieter(tmp_path):
    """The first version of the stereo mix used an `aformat` upmix, which takes 3 dB off each ear and put every
    line 3 dB lower against the beds than the mix was set for. The voice in each ear is the voice as recorded."""
    voice = tmp_path / "v.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=330:d=2", "-ar", "44100", "-ac", "1", str(voice)],
                   check=True, capture_output=True)
    out = tmp_path / "m.wav"
    # a volume other than 1 sends it down the mixing path; 1.001 is as good as none
    assert AudioMergerTool().run(out_path=str(out), placements=[{"path": str(voice), "start_ms": 0}], total_ms=2_000,
                                 dialogue_volume=1.001, loudness=False, level_voices=False).success
    heard = _channels(out)
    wanted = _channels(voice)[:, 0]
    n = min(len(heard), len(wanted))
    rms = lambda v: float(np.sqrt((v ** 2).mean()))
    for ear in (0, 1):
        assert abs(20 * np.log10(rms(heard[:n, ear]) / rms(wanted[:n]))) < 0.2


def test_a_mono_bed_and_the_same_bed_in_both_ears_sit_at_the_same_level(tmp_path):
    """A mono file given to the mixer (nothing makes one now, but a caller may) is put in both ears without the
    3 dB `aformat` takes off each: it ends where the same sound already in stereo does."""
    voice = tmp_path / "v.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=200:d=1", "-ar", "44100", "-ac", "1", str(voice)],
                   check=True, capture_output=True)
    mono = tmp_path / "mono.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=1000:d=4", "-ar", "44100", "-ac", "1", str(mono)],
                   check=True, capture_output=True)
    both = tmp_path / "both.wav"
    subprocess.run(["ffmpeg", "-y", "-i", str(mono), "-af", "pan=stereo|c0=c0|c1=c0", str(both)],
                   check=True, capture_output=True)
    levels = []
    for name, bed in (("a", mono), ("b", both)):
        out = tmp_path / f"{name}.wav"
        assert AudioMergerTool().run(out_path=str(out), placements=[{"path": str(voice), "start_ms": 0}],
                                     total_ms=4_000, bgm=str(bed), loudness=False, duck=False).success
        x = _channels(out)[int(2.0 * 44100):int(3.5 * 44100)]
        levels.append(20 * np.log10(np.sqrt((x ** 2).mean())))
    assert abs(levels[0] - levels[1]) < 0.3, levels
