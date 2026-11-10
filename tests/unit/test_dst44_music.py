"""DST-44 (item 3): where the music changes from one track to another it is crossfaded, not dipped.

Each scene's bed used to fade out and in over 350 ms, so a film that went from one track to another had
about 700 ms of music dropping away and coming back at every change.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from shared import music
from tests.unit.test_dst41_music_joins import SR, _deepest_dip_db, _hole_at, _samples


def _length_s(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def _two_tracks(tmp: Path, crossfade: bool):
    """Four seconds of one track, then four of another, joined the old way (faded) or crossfaded."""
    tmp.mkdir(parents=True, exist_ok=True)
    a, b = music.library()["tense-1"].path, music.library()["sad-1"].path
    half = music.CROSSFADE_MS // 2 if crossfade else 0
    first = music.cut_bed(a, 2_000, 4_000 + half, tmp / "a.wav", fade_in_ms=350,
                          fade_out_ms=0 if crossfade else 350)
    second = music.cut_bed(b, 6_000 - half, 4_000 + half, tmp / "b.wav", fade_in_ms=0 if crossfade else 350,
                           fade_out_ms=350)
    return music.join_crossfaded([first, second], [music.CROSSFADE_MS if crossfade else 0], tmp / "j.wav")


def test_a_crossfaded_join_is_exactly_as_long_as_the_beds_it_replaces(tmp_path):
    joined = _two_tracks(tmp_path, crossfade=True)
    assert abs(len(_samples(joined)) / SR - 8.0) < 0.003, _length_s(joined)


def _sag_db(path: Path, at: float, span: float = 0.6, window: float = 0.1) -> float:
    """How far the level (100 ms windows) falls around a point, below the music's usual level either side of it:
    what the ear hears as the music dropping away (a 2 ms window cannot tell this from the music's own dips)."""
    x = _samples(path)
    w = int(window * SR)

    def level(a: float, b: float) -> float:
        seg = x[int(a * SR): int(b * SR)]
        return 20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9)

    inside = min(level(t, t + window) for t in np.arange(at - span, at + span, window / 2))
    usual = np.median([level(t, t + window) for t in np.arange(at - 2.5, at + 2.5, window) if abs(t - at) > span + 0.2])
    return inside - usual


def test_a_change_of_track_no_longer_drops_away(tmp_path):
    """The music has quiet stretches of its own (this pair sags about 5 dB at 3 s whatever is done to the join), so
    the change is judged against those: the old join fell more than 20 dB, the crossfade no deeper than they do."""
    old_file = _two_tracks(tmp_path / "old", crossfade=False)
    new_file = _two_tracks(tmp_path / "new", crossfade=True)
    old, new = _sag_db(old_file, 4.0), _sag_db(new_file, 4.0)
    own = min(_sag_db(new_file, 3.0), _sag_db(new_file, 5.0))
    assert old < -15, f"the old join fell only {-old:.1f} dB: the defect is not there to see"
    assert new > own - 1.5, f"the crossfade falls {-new:.1f} dB, the music's own quiet stretches {-own:.1f}"
    assert new > old + 10


def test_no_single_sample_hole_at_the_change(tmp_path):
    """And nothing like the 30 to 50 ms hole DST-41 closed: the join is no deeper a dip than the music's own."""
    assert _hole_at(_two_tracks(tmp_path, crossfade=True), 4.0, (1.0, 2.0, 5.5, 6.5)) > -4


def test_through_the_crossfade_the_level_stays_where_the_music_is(tmp_path):
    """Equal power: the two tracks together are as loud as either alone (a plain linear crossfade of two
    different sounds sags 3 dB in the middle)."""
    joined = _two_tracks(tmp_path, crossfade=True)
    x = _samples(joined)

    def rms(a, b):
        seg = x[int(a * SR): int(b * SR)]
        return 20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9)

    around = [rms(3.0, 3.6), rms(4.4, 5.0)]
    middle = rms(3.8, 4.2)
    assert middle > min(around) - 2.5, (middle, around)


def test_a_join_with_no_crossfade_is_the_plain_join(tmp_path):
    a = music.cut_bed(music.library()["ambient-1"].path, 0, 2_000, tmp_path / "a.wav")
    b = music.cut_bed(music.library()["ambient-1"].path, 2_000, 2_000, tmp_path / "b.wav")
    one = music.join_crossfaded([a, b], [0], tmp_path / "x.wav")
    two = music.join([a, b], tmp_path / "y.wav")
    assert np.array_equal(_samples(one), _samples(two))


def test_the_overlaps_must_match_the_beds(tmp_path):
    a = music.cut_bed(music.library()["ambient-1"].path, 0, 1_000, tmp_path / "a.wav")
    with pytest.raises(ValueError):
        music.join_crossfaded([a, a], [0, 0], tmp_path / "x.wav")


# ---- in a film --------------------------------------------------------------------------------------

def _film_bgm(small_project, pid, choices):
    from agents.audio_agent import AudioAgent

    state, _sm = small_project(project_id=pid, with_bgm=True)
    state.audio.music_track = None
    for scene, choice in zip(state.script.scenes, choices):
        state.audio.scene_music[scene.scene_id] = choice
    agent = AudioAgent()
    return state, Path(agent._render_bgm(state))


def test_a_film_that_changes_track_has_its_music_as_long_as_its_timeline_and_no_dip_at_the_change(small_project):
    state, bgm = _film_bgm(small_project, "t_xf_film", ["tense-1", "sad-1", "sad-1"])
    total = state.audio.manifest.total_duration_ms / 1000.0
    assert abs(len(_samples(bgm)) / SR - total) < 0.004, (len(_samples(bgm)) / SR, total)
    cut = state.audio.manifest.scenes[1].start_ms / 1000.0
    elsewhere = [m for m in (cut / 2, cut + (total - cut) / 3) if abs(m - cut) > 0.5]
    assert _hole_at(bgm, cut, elsewhere) > -4, "a dip at the change of track"


def test_the_same_film_without_the_change_is_unaffected(small_project):
    state, bgm = _film_bgm(small_project, "t_xf_same", ["tense-1", "tense-1", "tense-1"])
    total = state.audio.manifest.total_duration_ms / 1000.0
    assert abs(len(_samples(bgm)) / SR - total) < 0.004


def test_a_scene_with_no_music_between_two_tracks_still_fades_and_the_length_holds(small_project):
    state, bgm = _film_bgm(small_project, "t_xf_none", ["tense-1", "none", "sad-1"])
    total = state.audio.manifest.total_duration_ms / 1000.0
    assert abs(len(_samples(bgm)) / SR - total) < 0.004


def test_a_scene_shorter_than_the_crossfade_does_not_change_the_length(small_project):
    from agents.audio_agent import AudioAgent

    state, _sm = small_project(project_id="t_xf_short", with_bgm=True)
    state.audio.music_track = None
    for scene, choice in zip(state.script.scenes, ["tense-1", "sad-1", "tense-1"]):
        state.audio.scene_music[scene.scene_id] = choice
    # a scene of a quarter of a second between two others
    t1 = state.audio.manifest.scenes[1]
    shrink = t1.duration_ms - 250
    t1.end_ms -= shrink
    for later in state.audio.manifest.scenes[2:]:
        later.start_ms -= shrink
        later.end_ms -= shrink
    state.audio.manifest.total_duration_ms -= shrink
    bgm = Path(AudioAgent()._render_bgm(state))
    assert abs(len(_samples(bgm)) / SR - state.audio.manifest.total_duration_ms / 1000.0) < 0.004
