"""The ffmpeg filter graph that joins clips, each cut with its own transition (DST-39).

One builder for both levels of the film: the shots of a scene (`animator.assemble_scene`) and the
scenes of the film (the `video.compose` tool). Each clip is as long as its shot plus the overlap
into the next, so a boundary of `n` overlap frames starts at the sum of the earlier clips'
*nominal* lengths: that is what keeps the film exactly the timeline's length (shared/transitions.py).

A boundary is `(xfade name | None, seconds)`. None is a straight cut, joined with `concat`.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

Boundary = Tuple[Optional[str], float]


def build_graph(durations_s: Sequence[float], boundaries: Sequence[Boundary]) -> Tuple[str, str]:
    """(filter_complex, label of the last stream) for N clips and N-1 boundaries.

    `durations_s[i]` is clip i's length *including* its trailing overlap.
    """
    n = len(durations_s)
    if len(boundaries) != max(0, n - 1):
        raise ValueError(f"{n} clips need {max(0, n - 1)} boundaries, got {len(boundaries)}")
    if n == 1:
        return "[0:v]null[v0]", "v0"
    parts: List[str] = []
    prev, acc = "0:v", float(durations_s[0])
    for i in range(1, n):
        name, d = boundaries[i - 1]
        out = f"v{i}"
        if name is None or d <= 0:
            parts.append(f"[{prev}][{i}:v]concat=n=2:v=1:a=0[{out}]")
            acc += durations_s[i]
        else:
            parts.append(f"[{prev}][{i}:v]xfade=transition={name}:duration={d:.6f}:"
                         f"offset={max(0.0, acc - d):.6f}[{out}]")
            acc += durations_s[i] - d
        prev = out
    return ";".join(parts), prev


def all_cuts(boundaries: Sequence[Boundary]) -> bool:
    return all(name is None or d <= 0 for name, d in boundaries)
