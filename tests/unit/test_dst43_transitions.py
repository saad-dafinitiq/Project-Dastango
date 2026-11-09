"""DST-43 (L1): the zoom never leaves the picture, and a transition says when it is held to less than asked."""
from __future__ import annotations

import subprocess

import numpy as np
import pytest

from agents.orchestrator.storyboard_view import cap_transitions
from shared import transitions, xfade

FPS = 24


def _clip(path, colour, seconds=1.5, size="160x90"):
    """A moving picture (so a flat frame would stand out), tinted `colour`."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"testsrc2=s={size}:r={FPS}:d={seconds}", "-vf", f"colorchannelmixer={colour},format=yuv420p",
                    "-c:v", "libx264", "-crf", "12", str(path)], check=True)
    return path


def _frames(path):
    """Every frame of a clip as a grey array."""
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = map(int, size.split(","))
    return np.frombuffer(out, np.uint8).reshape(-1, h, w)


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    d = tmp_path_factory.mktemp("zoom")
    return _clip(d / "a.mp4", "rr=1:gg=0.6:bb=0.6"), _clip(d / "b.mp4", "rr=0.6:gg=0.6:bb=1"), d


def _join(pair, kind, ms=500):
    a, b, d = pair
    n = round(ms * FPS / 1000)
    fps, size = xfade.probe_video(str(a))
    graph, last = xfade.build_graph([1.5, 1.5], [(kind, n / FPS)], fps=fps, size=size)
    out = d / f"{kind}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(a), "-i", str(b), "-filter_complex", graph,
                    "-map", f"[{last}]", "-c:v", "libx264", "-crf", "14", str(out)], check=True)
    return out, n, graph


def test_the_zoom_keeps_the_films_length_to_the_frame(pair):
    out, n, graph = _join(pair, transitions.ZOOM)
    assert "zoomthrough" not in graph and "xfade=transition=fade" in graph      # made of a scale and a fade
    assert len(_frames(out)) == 36 + 36 - n


def test_the_zoom_never_blurs_into_a_flat_frame(pair):
    out, n, _ = _join(pair, transitions.ZOOM, ms=700)
    frames = _frames(out)
    # the picture keeps its detail on every frame, through the whole overlap
    assert min(float(f.std()) for f in frames) > 15, [round(float(f.std())) for f in frames]
    # and it really moves: the last frame of the outgoing shot is not the first of the incoming one
    assert float(np.abs(frames[36 - n - 1].astype(int) - frames[36 + 1].astype(int)).mean()) > 3


def test_the_zoom_is_a_plain_fade_where_it_cannot_count_frames():
    graph, _ = xfade.build_graph([1.5, 1.5], [(transitions.ZOOM, 0.5)])
    assert "transition=fade" in graph and "zoomthrough" not in graph and "scale=" not in graph


def test_the_zoom_is_offered_on_every_server():
    assert "zoom" in [s.id for s in transitions.styles()]
    assert transitions.xfade_name("zoom") == transitions.ZOOM


def _cards(*scenes):
    """Scene cards with shots of the given lengths (ms) and a transition into each shot after the first."""
    cards, first = [], True
    for lengths in scenes:
        shots, t0 = [], 0
        for i, ln in enumerate(lengths):
            shots.append({"shot_id": f"s{len(cards)}_{i}", "start_ms": t0, "end_ms": t0 + ln,
                          "transition": None if first else {"kind": "zoom", "ms": 1200, "chosen": True, "between": i == 0}})
            first, t0 = False, t0 + ln
        cards.append({"shots": shots})
    return cards


def test_a_transition_longer_than_half_the_shorter_shot_is_said_to_be_held():
    cards = cap_transitions(_cards([1500, 1500]))
    t = cards[0]["shots"][1]["transition"]
    assert t["max_ms"] == 750 and t["capped"] is True            # the QA film: a 1.2 s zoom became 0.75 s


def test_a_short_neighbour_sets_the_limit_across_a_scene_join():
    cards = cap_transitions(_cards([3000, 800], [2000]))
    t = cards[1]["shots"][0]["transition"]
    assert t["max_ms"] == 400 and t["capped"] is True


def test_a_length_that_fits_is_not_capped_and_a_cut_never_is():
    cards = cap_transitions(_cards([4000, 4000]))
    assert cards[0]["shots"][1]["transition"]["capped"] is False
    cards = _cards([800, 800])
    cards[0]["shots"][1]["transition"]["kind"] = "cut"
    assert cap_transitions(cards)[0]["shots"][1]["transition"]["capped"] is False


def test_a_rendered_film_is_capped_by_its_real_shot_lengths():
    cards = cap_transitions(_cards([4000, 4000]), actual={"s0_0": 1000, "s0_1": 1000})
    assert cards[0]["shots"][1]["transition"]["max_ms"] == 500


def test_the_cards_a_storyboard_streams_carry_the_limit(small_project):
    from agents.orchestrator.storyboard_view import scene_cards

    state, _sm = small_project(project_id="t_d43_cap")
    cuts = [s["transition"] for card in scene_cards(state.script) for s in card["shots"] if s["transition"]]
    assert cuts and all("max_ms" in t and "capped" in t for t in cuts)
