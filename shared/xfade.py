"""The ffmpeg filter graph that joins clips, each cut with its own transition (DST-39).

One builder for both levels of the film: the shots of a scene (`animator.assemble_scene`) and the
scenes of the film (the `video.compose` tool). Each clip is as long as its shot plus the overlap
into the next, so a boundary of `n` overlap frames starts at the sum of the earlier clips'
*nominal* lengths: that is what keeps the film exactly the timeline's length (shared/transitions.py).

A boundary is `(xfade name | None, seconds)`. None is a straight cut, joined with `concat`.
"""
from __future__ import annotations

import subprocess
from typing import List, Optional, Sequence, Tuple

from shared.transitions import ZOOM

Boundary = Tuple[Optional[str], float]

# One clock for every stream in a join: xfade refuses two inputs that count time differently, and they do. A clip
# read from a file counts in its own units (1/12288 for a 24 fps x264 clip) and anything that has been through
# `concat` counts in microseconds, so a straight cut followed by any transition failed to render ("First input
# link main timebase (1/1000000) do not match ... (1/12288)"; found by rendering a zoom on a real film, DST-44,
# but it was true of every style). 1/90000 holds every frame time of 24, 25, 29.97, 30 and 60 fps whole.
CLOCK = "settb=expr=1/90000"

# How far the zoom pushes: the outgoing shot grows to this, the incoming one starts at it and settles.
ZOOM_PUSH = 0.28


def probe_video(path: str) -> Tuple[Optional[float], Optional[Tuple[int, int]]]:
    """(frame rate, (width, height)) of a clip, for the transitions that must count frames and keep
    the picture's size (the zoom). (None, None) when it cannot be read: the zoom is then a fade."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=r_frame_rate,width,height", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        width, height, rate = out.split(",")[:3]
        num, _, den = rate.partition("/")
        return float(num) / float(den or 1), (int(width), int(height))
    except (OSError, ValueError, ZeroDivisionError, subprocess.SubprocessError):
        return None, None


def _zoom_join(parts: List[str], prev: str, nxt: str, out: str, total_a: int, total_b: int, n: int,
               fps: float, tag: str) -> None:
    """Join `prev` (total_a frames) to `nxt` (total_b frames) over their last/first `n` frames with a
    push: the outgoing frames grow, the incoming ones settle from the same size, and a plain fade
    blends them. ffmpeg's own `zoomin` magnifies the last shot into a blur and then a flat colour;
    this never leaves the picture. The result has total_a + total_b - n frames, exactly what an
    xfade of n frames gives, because the overlap is cut out by frame number, not by time."""
    grow = f"1+{ZOOM_PUSH}*pow(n/{max(n - 1, 1)},2)"                # accelerating
    settle = f"1+{ZOOM_PUSH}*pow(1-n/{max(n - 1, 1)},2)"            # braking
    push = lambda expr: (f"scale=w='trunc(iw*({expr})/2)*2':h='trunc(ih*({expr})/2)*2':eval=frame:flags=bicubic,"
                         f"crop=w={{w}}:h={{h}},setsar=1,format=yuv420p")
    # One clock for every piece: xfade refuses two inputs that count time differently, and they do (the film so
    # far has been through filters, whose clock is 1/1000000; the next scene is a file, whose clock is its
    # own, 1/12288 for a 24 fps x264 clip). 1/90000 holds every frame time of 24, 25, 29.97, 30 and 60 fps whole.
    clock = CLOCK
    parts += [
        f"[{prev}]split[{tag}pa][{tag}pb]",
        f"[{nxt}]split[{tag}na][{tag}nb]",
        f"[{tag}pa]trim=end_frame={total_a - n},setpts=PTS-STARTPTS,{clock}[{tag}pre]",
        f"[{tag}pb]trim=start_frame={total_a - n},setpts=PTS-STARTPTS,{clock},{push(grow)}[{tag}tail]",
        f"[{tag}na]trim=end_frame={n},setpts=PTS-STARTPTS,{clock},{push(settle)}[{tag}head]",
        f"[{tag}nb]trim=start_frame={n},setpts=PTS-STARTPTS,{clock}[{tag}rest]",
        f"[{tag}tail][{tag}head]xfade=transition=fade:duration={n / fps:.6f}:offset=0[{tag}mid]",
        f"[{tag}pre][{tag}mid][{tag}rest]concat=n=3:v=1:a=0,{clock}[{out}]",     # concat counts in microseconds
    ]


def build_graph(durations_s: Sequence[float], boundaries: Sequence[Boundary],
                fps: Optional[float] = None, size: Optional[Tuple[int, int]] = None) -> Tuple[str, str]:
    """(filter_complex, label of the last stream) for N clips and N-1 boundaries.

    `durations_s[i]` is clip i's length *including* its trailing overlap. The zoom counts frames, so
    it needs `fps` and the clips' `size`; without them it is a plain fade.
    """
    clips = len(durations_s)
    if len(boundaries) != max(0, clips - 1):
        raise ValueError(f"{clips} clips need {max(0, clips - 1)} boundaries, got {len(boundaries)}")
    if clips == 1:
        return "[0:v]null[v0]", "v0"
    parts: List[str] = [f"[{i}:v]{CLOCK}[t{i}]" for i in range(clips)]
    prev, acc = "t0", float(durations_s[0])
    for i in range(1, clips):
        name, d = boundaries[i - 1]
        out = f"v{i}"
        if name is None or d <= 0:
            parts.append(f"[{prev}][t{i}]concat=n=2:v=1:a=0,{CLOCK}[{out}]")     # concat counts in microseconds
            acc += durations_s[i]
        elif name == ZOOM and fps and size and _can_zoom(acc, durations_s[i], d, fps):
            join: List[str] = []
            _zoom_join(join, prev, f"t{i}", out, round(acc * fps), round(durations_s[i] * fps),
                       round(d * fps), fps, f"z{i}")
            parts += [p.replace("{w}", str(size[0])).replace("{h}", str(size[1])) for p in join]
            acc += durations_s[i] - d
        else:
            name = "fade" if name == ZOOM else name
            parts.append(f"[{prev}][t{i}]xfade=transition={name}:duration={d:.6f}:"
                         f"offset={max(0.0, acc - d):.6f}[{out}]")
            acc += durations_s[i] - d
        prev = out
    return ";".join(parts), prev


def _can_zoom(acc: float, dur: float, d: float, fps: float) -> bool:
    """The zoom cuts frames out by number, so the overlap must be whole frames and fit in both clips."""
    n = round(d * fps)
    return n >= 2 and abs(d * fps - n) < 1e-3 and n <= round(acc * fps) and n <= round(dur * fps)


def all_cuts(boundaries: Sequence[Boundary]) -> bool:
    return all(name is None or d <= 0 for name, d in boundaries)
