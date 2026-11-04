"""Who is in the cast, and who has something to say (DST-33).

A character appears in the film only through the lines they speak, so a listed
character with none is drawn and voiced for nothing. This module finds those
characters, finds which of them the creator asked for by name, and removes the
rest. The story agent calls it once the script is written and any repairs are
done, before voices, portraits, the cast row and every file for the later phases.
"""
from __future__ import annotations
import re
from typing import Dict, Iterable, List, Sequence, Set

from shared.schemas.story import Character, ScriptOutput

# Name parts that prove nothing: a title, an age, a bare role or an article. A
# prompt saying "an old man" does not name a character called "Old Man", and a
# false match costs a creator a note about someone they never asked for.
GENERIC_WORDS = frozenset("""
the and for with from into over under of a an
narrator voice speaker storyteller
mr mrs ms miss dr sir lady lord madam master
old young little big small tiny tall elder elderly
man woman boy girl child kid men women people
captain officer private corporal sergeant general soldier guard
king queen prince princess
friend stranger villager mother father brother sister
protagonist antagonist supporting char character hero villain
""".split())


def _words(text: str) -> Set[str]:
    """Lower-case words of at least three letters, with a trailing 's removed."""
    found = re.findall(r"[A-Za-z][A-Za-z']+", text or "")
    return {w.lower().removesuffix("'s") for w in found if len(w) >= 3}


def speakers(script: ScriptOutput) -> Set[str]:
    """The ids of everyone with at least one line."""
    return {line.character_id for scene in script.scenes for line in scene.dialogue}


def silent_characters(script: ScriptOutput) -> List[Character]:
    """Cast members with no lines, in the order the roster lists them."""
    spoken = speakers(script)
    return [c for c in script.characters.characters if c.id not in spoken]


def _name_words(c: Character) -> Set[str]:
    """The words that say who a character is: their name, and their id.

    The id counts because a writer who calls the dog "Inu" still gives him
    `char_dog` (the first live run of the production prompt did exactly that), and
    the creator's prompt said "dog".
    """
    return _words(c.name) | _words(c.id.removeprefix("char_").replace("_", " "))


def named_in_prompt(characters: Iterable[Character], prompt: str) -> Set[str]:
    """The ids of the characters the creator's own prompt names.

    A character is named when a distinctive word of their name or id (three
    letters or more, not a title, age or bare role) is a word of the prompt. The
    narrator is never named: the creator did not ask for one, the film does.
    """
    prompt_words = _words(prompt)
    named: Set[str] = set()
    for c in characters:
        if c.role == "narrator":
            continue
        if (_name_words(c) - GENERIC_WORDS) & prompt_words:
            named.add(c.id)
    return named


def silent_but_named(script: ScriptOutput, prompt: str) -> List[Character]:
    """Silent characters the creator asked for by name."""
    silent = silent_characters(script)
    asked = named_in_prompt(silent, prompt)
    return [c for c in silent if c.id in asked]


def trim_silent_cast(script: ScriptOutput, prompt: str) -> List[Dict[str, object]]:
    """Remove every character with no lines, whatever their role. Returns who went.

    Never empties the cast: a script where nobody speaks is left as it is, since
    there is nobody to keep. The roster's order is kept for everyone who stays.
    A line, its speaker, the scenes and their pictures are not touched. Each
    record says whether the creator's prompt named that character, which is what
    decides whether the creator is told.
    """
    if not speakers(script):
        return []
    silent = silent_characters(script)
    if not silent:
        return []
    asked = named_in_prompt(silent, prompt)
    gone = {c.id for c in silent}
    script.characters.characters = [c for c in script.characters.characters if c.id not in gone]
    return [{"id": c.id, "name": c.name, "role": c.role, "named_in_prompt": c.id in asked}
            for c in silent]


def join_names(names: Sequence[str]) -> str:
    """'Kai', 'Kai and Aria', 'Kai, Aria and Dog': a list of names as a person says it."""
    names = [str(n) for n in names]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def removed_names_for_creator(removed: Iterable[Dict[str, object]]) -> List[str]:
    """Names the creator should hear about: only those their prompt named."""
    return [str(r["name"]) for r in removed if r.get("named_in_prompt")]
