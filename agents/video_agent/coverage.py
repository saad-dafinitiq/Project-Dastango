"""How a scene is shot: its shots, cuts, framings and camera moves (DST-36).

A film made of stills feels like a slideshow when a scene is one picture, or the
same few pictures again. Real editing covers a scene: an establishing wide shot,
then a medium, then a close-up on whoever is speaking, cutting when the speaker
changes. This module plans exactly that, from what the script and the timeline
already hold. It is pure planning, so the storyboard (which shows the shots
before anything is drawn) and the renderer (which cuts them) call the same code
and cannot disagree about what a scene looks like.

Cost. A close-up is the speaker's own portrait, drawn once per character for the
whole film. A medium shot is a crop of the scene's wide picture, which is free.
Only a long scene gets one more picture, a cutaway to a detail (config
coverage.cutaway_scene_s). So extra shots do not multiply image cost.

Timing. A cut is an absolute millisecond on the film's single timeline, the same
way every other cut is (shared/timeline.py): audio, picture and subtitles keep
their frame-exact agreement. This module only decides *where* the cuts fall; the
renderer turns them into frames.

Every number is in config/video.yaml with its reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from agents.video_agent import camera
from shared import video_quality
from shared.timeline import SCENE_PREROLL_MS, estimate_line_ms, LINE_GAP_MS, SCENE_TAIL_MS

# Where a medium shot looks within the picture. Slightly above centre, where
# faces and horizons usually sit, and alternating left and right so consecutive
# mediums do not frame the same part.
MEDIUM_X = (0.28, 0.72)
MEDIUM_Y = 0.42


@dataclass(frozen=True)
class CoverLine:
    """One spoken line, placed on the timeline (milliseconds from any one origin)."""
    character_id: str
    narrator: bool            # a narrator has no face to cut to
    start_ms: int
    end_ms: int
    text: str = ""


@dataclass(frozen=True)
class Cover:
    """One planned shot."""
    framing: str              # wide | medium | close | cutaway
    role: str                 # what the move is chosen for (camera.move_for_role)
    source: str               # wide | portrait | cutaway: which picture it is cut from
    start_ms: int
    end_ms: int
    character_id: Optional[str]            # whose face, for a close-up
    speakers: Tuple[str, ...]
    crop: Optional[Tuple[float, float, float]]   # (width fraction, x, y) or None
    move: str
    emotional: bool

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


# ---- how many shots, and whether a cutaway picture ------------------------------

def speaker_count(lines: Sequence[CoverLine], has_portrait: Callable[[str], bool]) -> int:
    """How many different people with a face to cut to speak in the scene."""
    return len({ln.character_id for ln in lines
                if not ln.narrator and has_portrait(ln.character_id)})


def shots_for(nominal_ms: int, speakers: int, lines: int = 0) -> int:
    """How many shots a scene is cut into: 2 to 4 (config coverage).

    The scene's length sets it (a typical shot is `typical_shot_s` long). A scene
    where two or more people talk gets at least one shot for each of them plus the
    wide, so the conversation can be cut back and forth, and a scene gets a shot
    for each of its lines (and the wide that opens it) as far as the cap allows,
    so a line starts on a cut whenever the cap lets it. It is decided from the
    script's own length, cast and lines, not from the voices, so the storyboard and
    the render agree on it.
    """
    q = video_quality.get()
    by_length = round(nominal_ms / 1000.0 / q.typical_shot_s)
    wanted = max(by_length, speakers + 1 if speakers >= 2 else 0, lines + 1 if lines else 0)
    n = max(q.min_shots, min(q.max_shots, wanted))
    # Never so many that a shot would be shorter than the shortest allowed.
    return max(1, min(n, int(nominal_ms / 1000.0 // q.min_shot_s)))


def needs_cutaway(nominal_ms: int) -> bool:
    """Does a scene this long get a second picture, a cutaway to a detail?"""
    return nominal_ms / 1000.0 >= video_quality.get().cutaway_scene_s


# ---- the plan ---------------------------------------------------------------------

@dataclass
class _Run:
    """A stretch of the scene with one subject: the world, one speaker, or several."""
    start_ms: int
    end_ms: int
    speakers: Tuple[str, ...] = ()
    emotional: bool = False
    hint: str = ""            # "", "medium", "cutaway": set when a run was split

    @property
    def who(self) -> Optional[str]:
        return self.speakers[0] if len(self.speakers) == 1 else None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def _runs(scene_start: int, scene_end: int, lines: Sequence[CoverLine], tone: str,
          has_portrait: Callable[[str], bool]) -> List[_Run]:
    """The scene as consecutive stretches: the pre-roll, then one per line.

    A line's stretch runs to the next line's start, so the gap after it and the
    tail belong to the line before them. A narrator's lines (and anyone with no
    portrait) belong to the world, which has no face to cut to.
    """
    runs: List[_Run] = []
    first = lines[0].start_ms if lines else scene_end
    if first > scene_start:
        runs.append(_Run(scene_start, first))
    for line in lines:
        who = None if (line.narrator or not has_portrait(line.character_id)) \
            else line.character_id
        runs.append(_Run(line.start_ms if runs else scene_start, scene_end,
                         (who,) if who else (), camera.is_emotional(tone, line.text)))
    for run, after in zip(runs, runs[1:]):
        run.end_ms = after.start_ms
    if runs:
        runs[-1].end_ms = scene_end
    return runs


def _merge(a: _Run, b: _Run) -> _Run:
    people = tuple(dict.fromkeys(a.speakers + b.speakers))
    return _Run(a.start_ms, b.end_ms, people, a.emotional or b.emotional)


def _shrink(runs: List[_Run], n: int) -> None:
    """Merge neighbouring stretches until there are `n`.

    Two stretches of the same speaker merge before two different ones, so a change
    of speaker stays a cut as long as it can; among equals the shortest pair goes.
    The opening stretch is kept apart where it can be, so the scene still opens
    on the world and not on a face.
    """
    while len(runs) > n:
        first = 0 if len(runs) <= 2 or n <= 1 else 1
        i = min(range(first, len(runs) - 1),
                key=lambda k: (runs[k].speakers != runs[k + 1].speakers,
                               runs[k + 1].end_ms - runs[k].start_ms))
        runs[i:i + 2] = [_merge(runs[i], runs[i + 1])]


def _grow(runs: List[_Run], n: int, min_ms: int, cutaway: bool) -> None:
    """Split the longest stretch until there are `n` (or none is long enough).

    A speaker's stretch becomes a medium and then the close-up; the world's becomes
    the wide and then a medium, or a cutaway when the scene has a second picture.
    """
    used_cutaway = False
    while len(runs) < n:
        splittable = [k for k, r in enumerate(runs) if r.duration_ms >= 2 * min_ms]
        if not splittable:
            return
        k = max(splittable, key=lambda j: runs[j].duration_ms)
        r = runs[k]
        if r.who:
            cut = r.start_ms + int(r.duration_ms * 0.4)
            head = _Run(r.start_ms, cut, r.speakers, r.emotional, hint="medium")
            tail = _Run(cut, r.end_ms, r.speakers, r.emotional)
        else:
            cut = r.start_ms + r.duration_ms // 2
            use_cutaway = cutaway and not used_cutaway and k > 0
            head = _Run(r.start_ms, cut, r.speakers, r.emotional, hint=r.hint)
            tail = _Run(cut, r.end_ms, r.speakers, r.emotional,
                        hint="cutaway" if use_cutaway else "medium")
            used_cutaway = used_cutaway or use_cutaway
        runs[k:k + 1] = [head, tail]


def plan_coverage(scene_start_ms: int, scene_end_ms: int, lines: Sequence[CoverLine],
                  tone: str, nominal_ms: int, scene_index: int = 0,
                  has_portrait: Callable[[str], bool] = lambda _id: True,
                  has_cutaway: bool = False) -> List[Cover]:
    """The shots of one scene, covering [scene_start_ms, scene_end_ms) exactly.

    `nominal_ms` is the script's length for the scene, which fixes how many shots
    there are; the line times decide where the cuts fall. Whoever is speaking is
    cut to; the scene opens on a wide shot that pans and closes on a pull-out.
    """
    q = video_quality.get()
    total = scene_end_ms - scene_start_ms
    n = shots_for(nominal_ms, speaker_count(lines, has_portrait), len(lines))
    n = max(1, min(n, total // int(q.min_shot_s * 1000)))
    min_ms = int(q.min_shot_s * 1000)

    runs = _runs(scene_start_ms, scene_end_ms, lines, tone, has_portrait)
    if not runs:
        runs = [_Run(scene_start_ms, scene_end_ms)]
    _shrink(runs, n)
    _grow(runs, n, min_ms, has_cutaway)

    covers: List[Cover] = []
    mediums = 0
    last = len(runs) - 1
    for i, r in enumerate(runs):
        crop = None
        character = None
        before = covers[-1] if covers else None
        # Two cuts in a row never land on the same framing of the same subject: a
        # speaker who talks twice running is a medium the second time, and the world
        # alternates between the wide picture and a medium of it.
        again = before is not None and before.speakers == r.speakers
        if r.hint == "cutaway":
            framing, source, role = "cutaway", "cutaway", "cutaway"
        elif i == 0:
            framing, source, role = "wide", "wide", "establishing"
        elif r.who and r.hint != "medium" and not (again and before.framing == "close"):
            framing, source, role, character = "close", "portrait", "dialogue", r.who
        elif not r.speakers and r.hint != "medium" and before is not None \
                and before.framing == "medium":
            framing, source, role = "wide", "wide", "narration"
        else:
            framing, source = "medium", "wide"
            role = "dialogue" if r.speakers else "narration"
            x = MEDIUM_X[(mediums + scene_index) % 2]
            crop = (q.medium_crop, x, MEDIUM_Y)
            mediums += 1
        # A scene closes on a pull-out, whatever the shot is; a lone shot just opens.
        move_role = "ending" if (i == last and len(runs) > 1) else role
        covers.append(Cover(
            framing=framing, role=role, source=source, start_ms=r.start_ms, end_ms=r.end_ms,
            character_id=character, speakers=r.speakers, crop=crop,
            move=camera.move_for_role(move_role, tone, r.emotional, scene_index, i),
            emotional=r.emotional))
    return covers


# ---- the same plan, before any voice exists -----------------------------------------

def estimated_lines(dialogue, people) -> Tuple[int, List[CoverLine]]:
    """The scene's lines laid out on a scene-relative timeline from the script alone.

    The storyboard has no recorded voices yet, so each line is as long as
    `estimate_line_ms` says, with the pre-roll, gaps and tail every render uses
    (shared/timeline.py). Returns (scene length, lines), both from zero.
    """
    cursor = SCENE_PREROLL_MS
    out: List[CoverLine] = []
    for i, line in enumerate(dialogue):
        if i:
            cursor += LINE_GAP_MS
        length = estimate_line_ms(line.text)
        person = people.get(line.character_id)
        out.append(CoverLine(line.character_id, bool(person and person.role == "narrator"),
                             cursor, cursor + length, line.text))
        cursor += length
    return cursor + SCENE_TAIL_MS, out


def plan_for_script(scene, people, has_portrait: Callable[[str], bool] = lambda _id: True
                    ) -> List[Cover]:
    """A scene's shots as the storyboard shows them (estimated times)."""
    total, lines = estimated_lines(scene.dialogue, people)
    if not lines:
        total = max(total, 1000)
    return plan_coverage(0, total, lines, getattr(scene, "tone", "") or "",
                         nominal_ms=scene.duration_ms, scene_index=scene.index,
                         has_portrait=has_portrait,
                         has_cutaway=needs_cutaway(scene.duration_ms))
