"""DST-41 (QA finding M3): no hole in the music or the ambience at a scene cut.

Each scene's bed was faded out and in over 20 ms before the beds were joined, so every cut of a film
had a 30 to 50 ms drop (to -70 dB in the finished mix at 11.0 s of the QA film), heard as a flutter
in sustained music. Where one track carries straight on across a cut, the join now has no fade.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from shared import music

SR = 22050


def _samples(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _deepest_dip_db(path: Path, at_s: float, window_s: float = 0.06) -> float:
    """The lowest 2 ms level around a point, in dB below the level either side of it."""
    x = _samples(path)
    i, w = int(at_s * SR), int(0.002 * SR)
    seg = x[i - int(window_s * SR): i + int(window_s * SR)]
    rms = np.array([np.sqrt((seg[k:k + w] ** 2).mean()) for k in range(0, len(seg) - w, w)])
    return 20 * np.log10((rms.min() + 1e-9) / (np.median(rms) + 1e-9))


def _hole_at(path: Path, join_s: float, elsewhere) -> float:
    """How much deeper the dip at a join is than the deepest dip anywhere else in the same sound (a
    low pad has quiet 2 ms stretches of its own at every zero crossing: a join is a hole only if it is
    worse than those). Near 0 dB: no hole. Large and negative: a hole."""
    return _deepest_dip_db(path, join_s) - min(_deepest_dip_db(path, at) for at in elsewhere)


def _joined(tmp: Path, fade_in, fade_out) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    src = music.library()["ambient-1"].path
    first = music.cut_bed(src, 5_000, 3_000, tmp / "a.wav", fade_in_ms=350, fade_out_ms=fade_out)
    second = music.cut_bed(src, 8_000, 3_000, tmp / "b.wav", fade_in_ms=fade_in, fade_out_ms=350)
    return music.join([first, second], tmp / "j.wav")


def test_a_fade_out_and_in_at_a_join_is_a_hole_and_no_fade_is_not(tmp_path):
    elsewhere = (1.0, 2.0, 4.5, 5.0)
    faded = _hole_at(_joined(tmp_path / "f", 20, 20), 3.0, elsewhere)          # how the beds were joined
    plain = _hole_at(_joined(tmp_path / "p", 0, 0), 3.0, elsewhere)
    assert faded < plain - 6, (faded, plain)


def test_a_track_that_carries_on_across_a_cut_has_no_hole(tmp_path):
    hole = _hole_at(_joined(tmp_path, 0, 0), 3.0, (1.0, 2.0, 4.5, 5.0))
    assert hole > -4, f"the join is {-hole:.1f} dB deeper a dip than anywhere else in the music"


def test_a_bed_with_no_fade_is_exactly_as_long_as_asked(tmp_path):
    src = music.library()["ambient-1"].path
    bed = music.cut_bed(src, 1_234, 2_345, tmp_path / "x.wav", fade_in_ms=0, fade_out_ms=0)
    assert abs(len(_samples(bed)) / SR - 2.345) < 0.02


def test_ambience_carries_on_across_a_cut_too(tmp_path):
    a = music.ambience_bed("wind", 3_000, tmp_path / "a.wav", fade_in_ms=300, fade_out_ms=0)
    b = music.ambience_bed("wind", 3_000, tmp_path / "b.wav", fade_in_ms=0, fade_out_ms=300)
    joined = music.join([a, b], tmp_path / "j.wav")
    assert _hole_at(joined, 3.0, (1.0, 2.0, 4.5, 5.0)) > -4
    # and a bed that starts or ends the ambience still fades there
    x = _samples(a)
    assert np.abs(x[: int(0.02 * SR)]).max() < 0.5 * np.abs(x[int(1.0 * SR): int(1.2 * SR)]).max()


def test_the_default_for_an_ambience_bed_still_fades_both_ends(tmp_path):
    x = _samples(music.ambience_bed("rain", 2_000, tmp_path / "r.wav"))
    mid = np.abs(x[int(0.8 * SR): int(1.2 * SR)]).max()
    assert np.abs(x[: int(0.02 * SR)]).max() < 0.3 * mid and np.abs(x[-int(0.02 * SR):]).max() < 0.3 * mid


@pytest.mark.parametrize("kind", ["bgm", "ambience"])
def test_a_film_with_one_track_or_ambience_has_no_hole_at_its_scene_cuts(small_project, kind):
    from agents.audio_agent import AudioAgent

    state, _sm = small_project(project_id=f"t_join_{kind}", with_bgm=True)
    agent = AudioAgent()
    if kind == "ambience":
        state.audio.ambience = {"*": "wind"}
        path = Path(agent._render_ambience(state))
    else:
        state.audio.music_track = "ambient-1"        # one track for the film: it carries on across every cut
        path = Path(agent._render_bgm(state))
    cuts = [t.start_ms / 1000.0 for t in state.audio.manifest.scenes[1:]]
    assert cuts, "the test film has no scene cuts"
    elsewhere = [m for m in (cuts[0] / 2, cuts[0] + (cuts[-1] - cuts[0]) / 3) if all(abs(m - c) > 0.3 for c in cuts)]
    for at in cuts:
        assert _hole_at(path, at, elsewhere) > -4, f"a hole in the {kind} at {at:.2f} s"
