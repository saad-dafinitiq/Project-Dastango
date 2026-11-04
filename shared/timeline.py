"""The film's single timeline — shared by audio, video, and subtitles.

Phase 2 decides *when* every dialogue line plays; Phase 3 cuts pictures to
those same boundaries, and subtitles read the same numbers. Keeping one
timeline is what keeps voices, faces, and captions in sync.

Per scene:

    |-- pre-roll --|-- line 1 --|gap|-- line 2 --|gap| ... |-- line N --|-- tail --|
    (establishing shot)

Video converts *absolute* millisecond boundaries to frames by rounding each
boundary (never by rounding durations), so rounding error can't accumulate:
every cut lands within half a frame of its audio position.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

SCENE_PREROLL_MS = 1500   # establishing shot before a scene's first line
LINE_GAP_MS = 250         # breathing room between consecutive lines
SCENE_TAIL_MS = 600       # hold after a scene's last line
EMPTY_SCENE_MS = 4000     # length of a scene with no dialogue (if the script gives none)

# Crossfade lengths. Every clip except the last in a chain is rendered this
# much longer so the overlap doesn't shorten the film (see xfade_frames()).
SHOT_XFADE_MS = 200       # between shots inside a scene
SCENE_XFADE_MS = 400      # between scenes

# Speaking-rate estimate used for planning before TTS exists. Kokoro is the
# shipped voice, so the rate is Kokoro's, measured in DST-10 run R (2026-10-29):
# 322 words in 122.5 s of speech over 39 lines in 7 films (the 8th, dst-05, was
# Urdu text a voice could not read, and is left out) = 2.63 words/s, median line
# 2.73. The M3 value of 2.2 was calibrated on edge-tts (2026-09-22), which
# speaks more slowly; planning at 2.2 for Kokoro made the estimate of speech
# time 7-37% too long in all 7 films, on top of scripts that were already short
# (DST-28). A film voiced by another engine lands off this estimate: edge-tts,
# at the 2.2 measured for it in M3, would speak ~18% longer than planned.
WORDS_PER_SECOND = 2.6
MIN_LINE_MS = 1200

# Lines a scene has, for budgeting before any line exists: 39 lines in 21
# scenes in the same run (1.86). Only the gaps between lines depend on it.
EXPECTED_LINES_PER_SCENE = 2


# How many words a spoken line has, for sizing a cast before any line exists:
# 8.1 over the 323 lines of the 46 model-written scripts of runs G and L
# (2026-10-29 and 2026-11-03; docs/EVAL.md, DST-33).
WORDS_PER_LINE = 8

# A character needs a few lines to be present in a film, not one: sizing the cast
# at 1.5 lines each leaves room for a short exchange for most of them.
LINES_PER_CHARACTER = 1.5
MIN_CAST, MAX_CAST = 2, 4     # narrator and one voice; "3-4 characters" was the old rule


@dataclass(frozen=True)
class LineSlot:
    scene_id: str
    line_id: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class SceneSlot:
    scene_id: str
    start_ms: int
    end_ms: int


def estimate_line_ms(text: str) -> int:
    """Rough spoken length of a line, used before real TTS durations exist."""
    words = max(1, len(text.split()))
    return max(MIN_LINE_MS, int(words / WORDS_PER_SECOND * 1000))


def scene_overhead_ms(line_count: int) -> int:
    """Non-speech time a scene adds around its lines."""
    if line_count == 0:
        return 0
    return SCENE_PREROLL_MS + SCENE_TAIL_MS + LINE_GAP_MS * (line_count - 1)


def expected_scene_overhead_ms() -> int:
    """`scene_overhead_ms` for a scene with the lines a scene usually has."""
    return scene_overhead_ms(EXPECTED_LINES_PER_SCENE)


def word_budget(target_s: float, scene_count: int) -> Tuple[int, int]:
    """(total, per scene) words of dialogue that make a film of `target_s` seconds.

    What is left of the target after every scene's pre-roll, tail and the gaps
    between its (expected) lines is speech, at the planning speaking rate. The
    story prompt budgets with this, so it cannot drift from the timeline's own
    numbers. (The offline template picks its lines one at a time with
    `estimate_line_ms`, the same rate, and needs no total.)
    """
    scene_count = max(1, scene_count)
    speech_s = max(0.0, target_s - scene_count * expected_scene_overhead_ms() / 1000)
    total = max(scene_count * 6, int(round(speech_s * WORDS_PER_SECOND)))
    return total, max(6, int(round(total / scene_count)))


def line_room(target_s: float, scene_count: int) -> int:
    """About how many lines of dialogue a film of `target_s` seconds has room for.

    From the word budget, so it moves with the length and the scene count the
    same way the script's own budget does. In the 46 scripts measured (runs G
    and L) models wrote 11% to 22% fewer words than budgeted (33 of 40, 54 of 60,
    77 of 93 and 98 of 125 words at 20, 30, 45 and 60 s), so this reads high: 4.9,
    7.4, 11.5 and 15.4 against 3.9, 6.8, 9.0 and 11.5 lines. The cast limit is
    capped, so that does not matter above the shortest films.
    """
    total, _ = word_budget(target_s, scene_count)
    return max(max(1, scene_count), int(round(total / WORDS_PER_LINE)))


def cast_limit(target_s: float, scene_count: int) -> int:
    """The most characters a film this long can give something to say (DST-33).

    A 20 s film has about four lines: four characters cannot each have one, and
    the ones left over are drawn and voiced for nothing. Characters the creator
    names in their own prompt are not bound by this (the story prompt says so).
    """
    wanted = int(round(line_room(target_s, scene_count) / LINES_PER_CHARACTER))
    return max(MIN_CAST, min(MAX_CAST, wanted))


def cast_range(target_s: float, scene_count: int) -> Tuple[int, int]:
    """(fewest, most) characters the story prompt asks for.

    The first live plan run on a prompt that only capped the cast and asked for "the
    characters this story needs" (2026-11-05) made 6 of 25 films a one-speaker
    monologue, against 1 of 24 before: told to list only what the story needs, models
    list the minimum. So the prompt names a range, and a lone voice has to be a
    deliberate choice.
    """
    most = cast_limit(target_s, scene_count)
    return min(MIN_CAST, most), most


def planned_length_ms(scene_lines: Sequence[Sequence[str]]) -> int:
    """How long the film will run, before any voice exists, from each scene's
    spoken lines: the timeline itself, with `estimate_line_ms` as every line's
    length. The same arithmetic as `build_timeline`, so it matches what a
    render lays out (to within the voice's real speed)."""
    scenes = [(f"s{i}", [(f"s{i}l{j}", estimate_line_ms(text)) for j, text in enumerate(lines)], 0)
              for i, lines in enumerate(scene_lines)]
    return build_timeline(scenes)[2]


def build_timeline(
    scenes: Sequence[Tuple[str, Sequence[Tuple[str, int]], int]],
) -> Tuple[List[SceneSlot], List[LineSlot], int]:
    """Lay scenes and lines out on one absolute timeline.

    `scenes` is a list of (scene_id, [(line_id, duration_ms), ...], empty_scene_ms).
    `empty_scene_ms` is only used when a scene has no lines.
    Returns (scene_slots, line_slots, total_ms).
    """
    cursor = 0
    scene_slots: List[SceneSlot] = []
    line_slots: List[LineSlot] = []
    for scene_id, lines, empty_ms in scenes:
        start = cursor
        if not lines:
            cursor += max(1000, empty_ms or EMPTY_SCENE_MS)
        else:
            cursor += SCENE_PREROLL_MS
            for i, (line_id, dur) in enumerate(lines):
                if i > 0:
                    cursor += LINE_GAP_MS
                line_slots.append(LineSlot(scene_id, line_id, cursor, cursor + dur))
                cursor += dur
            cursor += SCENE_TAIL_MS
        scene_slots.append(SceneSlot(scene_id, start, cursor))
    return scene_slots, line_slots, cursor


def ms_to_frame(ms: float, fps: int) -> int:
    """Absolute time -> nearest frame index."""
    return int(round(ms * fps / 1000.0))


def span_frames(start_ms: float, end_ms: float, fps: int) -> int:
    """Frame count for [start, end) using absolute-boundary rounding."""
    return ms_to_frame(end_ms, fps) - ms_to_frame(start_ms, fps)


def xfade_frames(xfade_ms: int, fps: int) -> int:
    """Crossfade length in whole frames (at least one)."""
    return max(1, int(round(xfade_ms * fps / 1000.0)))


def split_span(start_ms: int, end_ms: int, max_ms: int, min_ms: int) -> List[Tuple[int, int]]:
    """Split [start, end) into near-equal pieces no longer than `max_ms`.

    Pieces never drop below `min_ms` (the span is left whole if it can't be
    split that way).
    """
    total = end_ms - start_ms
    if total <= max_ms:
        return [(start_ms, end_ms)]
    n = -(-total // max_ms)  # ceil
    while n > 1 and total / n < min_ms:
        n -= 1
    if n <= 1:
        return [(start_ms, end_ms)]
    bounds = [start_ms + round(total * k / n) for k in range(n + 1)]
    return [(bounds[k], bounds[k + 1]) for k in range(n)]


def scenes_by_id(slots: Sequence[SceneSlot]) -> Dict[str, SceneSlot]:
    return {s.scene_id: s for s in slots}
