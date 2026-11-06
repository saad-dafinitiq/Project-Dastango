"""M3 — the master mix: music ducks under dialogue and loudness is normalised."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

from mcp.tool_executor import ToolExecutor


def _tone(path: Path, freq: int, seconds: float, volume: float = 0.5) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-af", f"volume={volume}", "-ar", "22050", "-ac", "1", str(path)],
        check=True, capture_output=True)
    return str(path)


def _rms_db(path: str, start: float, end: float, band: int | None = None) -> float:
    """Mean volume of a slice, in dB; `band` isolates one tone's frequency."""
    filters = (f"bandpass=f={band}:width_type=h:w=120," if band else "") + "volumedetect"
    proc = subprocess.run(
        ["ffmpeg", "-i", str(path), "-ss", f"{start}", "-to", f"{end}",
         "-af", filters, "-f", "null", "-"],
        capture_output=True, text=True)
    for line in proc.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    raise AssertionError("volumedetect produced no mean_volume")


def _loudness_lufs(path: str) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
         "-f", "null", "-"], capture_output=True, text=True)
    blob = proc.stderr[proc.stderr.rindex("{"):proc.stderr.rindex("}") + 1]
    return float(json.loads(blob)["input_i"])


def _mix(tmp_path: Path, name: str, duck: bool, loudness: bool = False, seconds: float = 4.0) -> str:
    """Dialogue in the first 2s only; music for the whole of `seconds` (4 by default)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    speech = _tone(tmp_path / "speech.wav", 300, 2.0, volume=0.6)
    music = _tone(tmp_path / "music.wav", 900, seconds, volume=0.6)
    out = tmp_path / f"{name}.wav"
    res = ToolExecutor().execute(
        "audio.merge", out_path=str(out), total_ms=int(seconds * 1000),
        placements=[{"path": speech, "start_ms": 0}],
        bgm=music, bgm_volume=0.5, duck=duck, loudness=loudness,
    )
    assert res.success, res.error
    assert res.metadata["ducked"] is duck
    return res.data


def test_music_ducks_while_someone_speaks(tmp_path):
    """Speech is a 300 Hz tone, music a 900 Hz tone, so each can be measured alone."""
    # A long bed, so there is time after the line for the compressor to let go: how fast it does depends on
    # the ffmpeg build (the music is back within about 1 s on one, and still creeping up 5 s later on another,
    # which is what made a measurement at 3 s to 4 s flaky), so the climb is judged late in the bed.
    ducked = _mix(tmp_path / "a", "ducked", duck=True, seconds=9.0)
    flat = _mix(tmp_path / "b", "flat", duck=False, seconds=9.0)
    MUSIC, SPEECH = 900, 300

    # The music is pushed down while the line plays (measured: ~6 dB)...
    during = _rms_db(ducked, 0.5, 1.8, MUSIC)
    duck_amount = _rms_db(flat, 0.5, 1.8, MUSIC) - during
    assert duck_amount > 4, f"music only ducked by {duck_amount:.1f} dB"

    # ...and comes back after it: later in the bed it is clearly above where it was under the line, and it
    # has not stopped climbing (a soft release, no pumping).
    soon = _rms_db(ducked, 3.0, 4.0, MUSIC) - during
    late = _rms_db(ducked, 6.0, 7.5, MUSIC) - during
    assert late > 1.0, f"music only recovered {late:.1f} dB after the line"
    assert late >= soon - 0.3, f"music was {soon:.1f} dB back at 3 s but {late:.1f} dB at 6 s: it fell again"

    # ...and the dialogue itself is untouched.
    assert abs(_rms_db(ducked, 0.2, 1.8, SPEECH) - _rms_db(flat, 0.2, 1.8, SPEECH)) < 1.0


def test_master_is_loudness_normalised(tmp_path):
    (tmp_path / "c").mkdir(parents=True, exist_ok=True)
    quiet_speech = _tone(tmp_path / "c" / "speech.wav", 300, 3.0, volume=0.02)
    out = tmp_path / "c" / "master.wav"
    res = ToolExecutor().execute(
        "audio.merge", out_path=str(out), total_ms=3000,
        placements=[{"path": quiet_speech, "start_ms": 0}], loudness=True,
    )
    assert res.success and res.metadata["loudness"] is True
    # A very quiet source is brought up near the -16 LUFS target.
    assert -22 < _loudness_lufs(res.data) < -12


def test_normalisation_does_not_change_length(tmp_path):
    (tmp_path / "d").mkdir(parents=True, exist_ok=True)
    speech = _tone(tmp_path / "d" / "speech.wav", 300, 1.5)
    out = tmp_path / "d" / "master.wav"
    res = ToolExecutor().execute(
        "audio.merge", out_path=str(out), total_ms=4000,
        placements=[{"path": speech, "start_ms": 500}], loudness=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", res.data],
        capture_output=True, text=True, check=True)
    assert abs(float(probe.stdout) * 1000 - 4000) < 60
