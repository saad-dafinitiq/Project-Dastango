"""Holding a script to the number of scenes the creator asked for (DST-34).

Length and cast are checked and repaired (DST-28, DST-33); the scene count was only
*requested* in the prompt, so a model that wrote five scenes for a "three scenes" film
simply got five. This is the check, a deterministic last resort for too many scenes
(merging the two shortest neighbours, which keeps the story and the cast), and the
renumbering both repairs need so scene ids and line ids stay consistent.

Too few scenes cannot be repaired without inventing story, so that is reported
(`scene_check`) and shown to the creator instead of being hidden.
"""
from __future__ import annotations

from typing import Dict

from shared.schemas.story import DialogueLine, Scene, ScriptOutput


def renumber(script: ScriptOutput) -> None:
    """scene_1, scene_2, ... with index and line ids to match, in order."""
    for i, scene in enumerate(script.scenes):
        scene.scene_id = f"scene_{i + 1}"
        scene.index = i
        for j, line in enumerate(scene.dialogue):
            line.line_id = f"{scene.scene_id}_l{j + 1}"
        scene.shot_overrides = {}      # keyed by the old ids; a new plan starts clean


def _words(scene: Scene) -> int:
    return sum(len(ln.text.split()) for ln in scene.dialogue)


def merge_to(script: ScriptOutput, count: int) -> int:
    """Merge the shortest pair of neighbouring scenes until there are `count`. Returns the
    number of merges. The first scene of a pair keeps its title, place, tone and picture; the
    second's lines follow the first's and their time is added, so the film's length does not
    move and nobody loses their lines."""
    merges = 0
    while len(script.scenes) > max(1, count):
        pairs = [(_words(a) + _words(b) + (a.duration_ms + b.duration_ms) // 1000, i)
                 for i, (a, b) in enumerate(zip(script.scenes, script.scenes[1:]))]
        _, i = min(pairs)
        a, b = script.scenes[i], script.scenes[i + 1]
        a.dialogue = list(a.dialogue) + [DialogueLine(**ln.model_dump()) for ln in b.dialogue]
        a.duration_ms += b.duration_ms
        del script.scenes[i + 1]
        merges += 1
    renumber(script)
    return merges


def scene_check(asked: int, script: ScriptOutput, repairs: int, merged: int) -> Dict[str, object]:
    """What the scene-count check found, kept on the project like the length and cast checks."""
    got = len(script.scenes)
    return {"asked": asked, "got": got, "repairs": repairs, "merged": merged, "within": got == asked}
