"""Edit-intent classification.

Two paths:
1. LLM-backed structured-output classifier (Gemini/OpenAI/Claude) when an API
   key is configured. Returns a validated EditIntent.
2. A deterministic keyword + semantic fallback that runs offline. Tests
   exercise this path so the project always has reliable intent detection.
"""
from __future__ import annotations
import re
from typing import Dict, List, Optional, Tuple

from mcp.tools.llm_tools.llm_client import get_llm_client
from mcp.tools.vision_tools.image_edit_tool import list_filter_names
from shared import music, transitions
from shared.schemas.edit import EditIntent
from shared.utils.logging import get_logger

from .vocabulary import (EDITS, UNCLEAR, EditDraft, describe_for_model, draft_parameters,
                         missing, normalise_scope)

log = get_logger("intent_classifier")


# (keyword regex, intent, target, parameters_extractor)
_RULES: List[Tuple[re.Pattern, str, str, str]] = [
    # Audio --------------------------------------------------------------
    # Inflections count: "voices", "narrator", "whispered", "deeper", "warmer".
    (re.compile(r"\b(voices?|tone|speak\w*|narrat\w*)\b.*"
                r"\b(tone|whisper\w*|deep(er)?|cheer\w*|warm(er)?|angry|softer|louder)\b", re.I),
     "change_voice_tone", "audio", "tone"),
    (re.compile(r"\b(change|set|make).*\bvoices?\b", re.I),
     "change_voice", "audio", "voice"),
    (re.compile(r"\b(louder|quieter|volume|softer)\b", re.I),
     "adjust_volume", "audio", "volume"),
    (re.compile(r"\b(add|put|include).*\b(background music|bgm|soundtrack|music)\b", re.I),
     "add_background_music", "audio", "mood"),
    (re.compile(r"\b(remove|delete|drop).*\b(background music|bgm|music)\b", re.I),
     "remove_background_music", "audio", ""),
    (re.compile(r"\bregenerate.*\b(audio|voice|tts|narration)\b", re.I),
     "regenerate_audio", "audio", ""),
    # Video frame --------------------------------------------------------
    (re.compile(r"\b(make|set).*\b(scene|image|shot)\b.*\b(dark|darker|brighter|moody|gloom)\b", re.I),
     "adjust_scene_aesthetic", "video_frame", "aesthetic"),
    (re.compile(r"\b(change|update|alter).*\bcharacter\b.*\b(design|appearance|look)\b", re.I),
     "change_character_design", "video_frame", "character"),
    (re.compile(r"\b(regenerate|redo|remake).*\b(scene|image|frame)\b", re.I),
     "regenerate_scene", "video_frame", "scene"),
    (re.compile(r"\bapply.*\b(filter|style)\b", re.I),
     "apply_filter", "video_frame", "filter"),
    (re.compile(r"\b(grayscale|sepia|vintage|noir|cinematic|dreamy|warm|cool|pastel)\b", re.I),
     "apply_filter", "video_frame", "filter_name"),
    # Video --------------------------------------------------------------
    (re.compile(r"\b(remove|delete|drop|hide).*\bsubtitle", re.I),
     "remove_subtitles", "video", ""),
    (re.compile(r"\b(add|enable|turn on).*\bsubtitle", re.I),
     "add_subtitles", "video", ""),
    (re.compile(r"\b(speed up|faster|quicken)\b", re.I),
     "speed_up", "video", "factor"),
    (re.compile(r"\b(slow down|slower)\b", re.I),
     "slow_down", "video", "factor"),
    (re.compile(r"\b(recompose|recompile|rebuild|rerender|re-render).*\bvideo\b", re.I),
     "recompose_video", "video", ""),
    # Script -------------------------------------------------------------
    (re.compile(r"\b(regenerate|rewrite|redo).*\b(script|story|plot|dialogue)\b", re.I),
     "regenerate_script", "script", ""),
    (re.compile(r"\b(change|alter|update).*\b(genre|tone|mood)\b", re.I),
     "change_genre", "script", "genre"),
]


# Every filter / style preset the image edit tool can apply, longest first so
# e.g. "cold_thriller" wins over "cool". Kept in sync with the tool itself.
_FILTER_NAMES = sorted(list_filter_names(), key=lambda n: (-len(n), n))

_TONES = {
    "whisper": "whispered", "whispered": "whispered",
    "deep": "deep", "warm": "warm", "cheer": "cheerful",
    "angry": "angry", "sad": "sad", "anxious": "anxious",
    "soft": "soft", "loud": "loud",
}

_MOODS = {
    "ambient", "tense", "joyful", "mysterious", "epic", "sad",
    "ominous", "ethereal", "energetic", "neutral",
}


_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10}
_NUMBER = "|".join(_NUMBER_WORDS)


def numerals(q: str) -> str:
    """"scene two" -> "scene 2", "scenes one and three" -> "scenes 1 and 3". Only after a word that
    takes a number, so "the one in scene 3" keeps its "one"."""
    def digit(m: re.Match) -> str:
        return f"{m.group(1)}{_NUMBER_WORDS[m.group(2)]}"

    q = re.sub(rf"\b((?:scenes?|shots?|lines?)\s+)({_NUMBER})\b", digit, q)
    return re.sub(rf"\b((?:scenes?|shots?)\s+\d+\s*(?:and|&|to|,)\s*)({_NUMBER})\b", digit, q)


# A new request starts with one of these ("add rain AND make the music quieter"). The word after "and"
# is what tells a second request from "scenes 1 and 2" or "the music and the rain".
_STARTS = ("add|make|remove|turn|switch|play|use|change|set|put|give|mute|redraw|regenerate|remake|redo|move|"
           "delete|swap|show|hide|apply|slow|speed|lower|raise|increase|decrease|drop|take|get|include|"
           "enable|disable|no|without|rewrite|stop|start|swap|cut|bring|keep")
_SPLIT = re.compile(rf"\s*(?:[;,]|\b(?:and then|and also|and|then|also|plus)\b)\s*(?:(?:and|then|also)\s+)?"
                    rf"(?=(?:{_STARTS})\b)", re.I)


def _level_words(q: str) -> Optional[int]:
    """The level a sentence asks for, in percent of "as made" ("quieter" 60, "much louder" 200, "at 50%"), or None."""
    louder = re.search(r"\b(louder|turn\b[^.]*\bup|raise|boost|increase)\b", q)
    quieter = re.search(r"\b(quieter|softer|lower|turn\b[^.]*\bdown|reduce|decrease|less)\b", q)
    pct = re.search(r"(\d{1,3})\s*(%|percent)", q)
    if not (pct or louder or quieter):
        return None
    much = bool(re.search(r"\b(much|a lot|way|far)\b", q))
    return int(pct.group(1)) if pct else (200 if louder and much else 150 if louder else 30 if much else 60)


# Two things for one change: "make the music and the rain quieter", "make the music quieter and the rain louder".
# (the voices are in the list only so that a sentence naming them is split and the voice part is reported as
# not done, instead of the music part quietly standing for both)
_SOUNDS = r"(?:music|soundtrack|score|bgm|rain|wind|sea|ocean|city|traffic|night|ambience|ambient sound|voices?|dialogue|narration)"
_LEVEL_ADJ = r"(?:quieter|louder|softer|lower|higher|down|up|much quieter|much louder|a lot quieter|a lot louder)"
_BOTH = re.compile(rf"^(?P<verb>\w+)\s+(?P<a>(?:the\s+)?{_SOUNDS})\s+and\s+(?P<b>(?:the\s+)?{_SOUNDS})\s+(?P<adj>{_LEVEL_ADJ})$", re.I)
_EACH = re.compile(rf"^(?P<verb>\w+)\s+(?P<a>(?:the\s+)?{_SOUNDS})\s+(?P<adj1>{_LEVEL_ADJ})\s+and\s+(?P<b>(?:the\s+)?{_SOUNDS})\s+(?P<adj2>{_LEVEL_ADJ})$", re.I)
_LEADING_SCENE = re.compile(r"^(?:in|for|during|on)\s+(?P<scene>scenes?\s*\d+)\s*[,:]?\s+(?P<rest>.+)$", re.I)


def _expand(part: str) -> List[str]:
    """One part naming two sounds for one change becomes one part each."""
    for rx, forms in ((_EACH, (("a", "adj1"), ("b", "adj2"))), (_BOTH, (("a", "adj"), ("b", "adj")))):
        m = rx.match(part.strip())
        if m:
            return [f"{m.group('verb')} {m.group(a)} {m.group(adj)}" for a, adj in forms]
    return [part]


def split_request(query: str) -> List[str]:
    """A sentence that asks for several things, as one request each ("add rain and make the music
    quieter" -> two; "make the music and the rain quieter" -> two). Quoted text is kept whole. One
    request comes back as itself.

    A scene named first covers every part ("in scene 2, add rain and make the music quieter" is both in
    scene 2); a scene named inside one part stays with that part ("add rain in scene 2 and make the music
    quieter": the music is the whole film's, and the answer says so)."""
    held: List[str] = []

    def hold(m: re.Match) -> str:
        held.append(m.group(0))
        return f"\x00{len(held) - 1}\x00"

    masked = re.sub(r"[\"“][^\"”]*[\"”]", hold, query)
    lead = _LEADING_SCENE.match(masked.strip())
    scene = lead.group("scene") if lead else None
    if lead:
        masked = lead.group("rest")
    parts = [p.strip(" .") for p in _SPLIT.split(masked)]
    parts = [q for p in parts if p.strip(" .") for q in _expand(p)]
    parts = [re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], p) for p in parts]
    if scene and len(parts) > 1:
        parts = [p if re.search(r"\bscenes?\s*\d+", p, re.I) else f"{p} in {scene}" for p in parts]
    return parts if len(parts) > 1 else [query.strip()]


SYSTEM_PROMPT = """You turn a creator's request to change their short animated film
into one edit the editor can carry out. Only the intents and values listed are
possible; never invent new ones. If the request matches none, use "unclear".
Always reply with one valid JSON object."""


CLASSIFICATION_PROMPT = """Request: "{query}"

Scenes: {scenes}
Characters: {chars}

Fill in this form as one JSON object with exactly the keys intent, scope, tone,
volume, mood, filter, aesthetic, factor, genre, reasoning.

{form}
scope — "global", "scene:<scene id>" or "character:<character id>": the part of
the film the request is about.
reasoning — one short sentence.
"""


class IntentClassifier:
    def __init__(self):
        self.llm = get_llm_client("edit_intent")

    # ---- public API ------------------------------------------------------

    def classify(self, query: str, scenes: List[str] | None = None,
                 characters: List[str] | None = None,
                 character_names: Dict[str, str] | None = None,
                 scene_titles: Dict[str, str] | None = None) -> EditIntent:
        """`characters` are ids (char_protagonist); `character_names` maps id -> name
        (Aria) so users can refer to characters the way the story does."""
        names = character_names or {}
        scenes, characters = scenes or [], characters or []
        keyword = self._keyword_classify(query, scenes, characters, names)
        if self.llm.provider == "mock":
            return keyword
        try:
            intent = self._llm_classify(query, scenes, characters, names, scene_titles or {})
        except Exception as e:  # noqa: BLE001
            log.warning("LLM intent classification failed (%s) — using keyword fallback", e)
            return keyword
        if intent.intent == UNCLEAR or missing(intent.intent, intent.parameters):
            # The keyword rules sometimes catch what the model gave up on.
            if keyword.intent in EDITS and not missing(keyword.intent, keyword.parameters):
                log.info("model answered %s; keyword rules found %s", intent.intent,
                         keyword.intent)
                return keyword
        return intent

    # ---- LLM path --------------------------------------------------------

    def _llm_classify(self, query: str, scenes: List[str], characters: List[str],
                      names: Dict[str, str], titles: Dict[str, str]) -> EditIntent:
        chars = ", ".join(f"{c} ({names[c]})" if c in names else c for c in characters)
        prompt = CLASSIFICATION_PROMPT.format(
            query=query,
            scenes=", ".join(f"{s} ({titles[s]})" if s in titles else s for s in scenes)
            or "scene_1, scene_2, scene_3, scene_4",
            chars=chars or "char_narrator, char_protagonist, char_supporting",
            form=describe_for_model(),
        )
        draft = self.llm.generate_structured(
            prompt=prompt,
            schema=EditDraft,
            system=SYSTEM_PROMPT,
            temperature=0.1,
        )
        return self.from_draft(draft, scenes, characters, names)

    @staticmethod
    def from_draft(draft: EditDraft, scenes: List[str], characters: List[str],
                   names: Dict[str, str]) -> EditIntent:
        """The model's form, in the names the planner and executor read."""
        if draft.intent == UNCLEAR:
            return EditIntent(intent=UNCLEAR, target="video", scope="global",
                              confidence=0.3, reasoning=draft.reasoning)
        return EditIntent(
            intent=draft.intent,
            target=EDITS[draft.intent].target,
            scope=normalise_scope(draft.scope, scenes, characters, names),
            parameters=draft_parameters(draft),
            confidence=0.85,
            reasoning=draft.reasoning,
        )

    # ---- keyword path ----------------------------------------------------

    def _keyword_classify(self, query: str, scenes: List[str],
                          characters: List[str], names: Dict[str, str] | None = None
                          ) -> EditIntent:
        q = numerals(query.lower().strip())

        # Match a scene mention if present.
        scope = "global"
        scene_match = re.search(r"scene[_\s]*(\d+)", q)
        if scene_match:
            scope = f"scene:scene_{scene_match.group(1)}"
        else:
            for s in scenes:
                if s.lower() in q:
                    scope = f"scene:{s}"
                    break

        transition = self._transition_intent(q, scenes)
        if transition:
            return transition
        sound = self._sound_intent(q, scope)
        if sound:
            return sound
        titles = self._graphics_intent(query, q, scope)
        if titles:
            return titles
        look = self._effect_intent(q, scope)
        if look:
            return look
        scene_change = self._scene_intent(query, q)
        if scene_change:
            return scene_change

        # Match a character mention (by name, e.g. "Aria", or id, e.g. "protagonist").
        char_scope = ""
        for c in characters:
            aliases = {c.replace("char_", "").lower(), (names or {}).get(c, "").lower()}
            if any(a and re.search(rf"\b{re.escape(a)}\b", q) for a in aliases):
                char_scope = f"character:{c}"
                break
        if not scene_match and char_scope:
            scope = char_scope

        # Apply rule-based intent matches.
        for pattern, intent, target, param_kind in _RULES:
            if pattern.search(q):
                params = self._extract_params(q, param_kind)
                if char_scope and target == "audio":
                    scope = char_scope
                return EditIntent(
                    intent=intent,
                    target=target,
                    scope=scope,
                    parameters=params,
                    confidence=0.78,
                    reasoning=f"keyword match on '{pattern.pattern[:50]}'",
                )

        # Generic fallback: try to detect target by topic words.
        target = "video"
        if any(w in q for w in ("voice", "narration", "tts", "music", "audio", "louder", "quieter")):
            target = "audio"
        elif any(w in q for w in ("scene", "image", "frame", "filter", "darker", "brighter")):
            target = "video_frame"
        elif any(w in q for w in ("script", "story", "plot", "dialogue")):
            target = "script"
        return EditIntent(
            intent="generic_edit",
            target=target,
            scope=scope,
            parameters={"raw_query": query},
            confidence=0.4,
            reasoning="no keyword rule matched; falling back to topic heuristic",
        )

    @staticmethod
    def _sound_intent(q: str, scope: str) -> Optional[EditIntent]:
        """The music, the ambience and how loud things are in the mix: "make the music quieter",
        "no music in scene 2", "play something tense in scene 3", "add rain to scene 1".
        "Add/remove background music" is left to the older rules (they end up in the same operation)."""
        music_word = re.search(r"\b(music|soundtrack|score|bgm|song)\b", q) or \
            re.search(r"\b(play|put on|i want|give me)\s+(me\s+)?(something|a track|a song|some tunes)\b", q)
        kinds = {"rain": r"\brain(y|ing)?\b", "wind": r"\bwind(y)?\b", "sea": r"\b(sea|ocean|waves)\b",
                 "city": r"\b(city|traffic|street)\b", "night": r"\b(crickets|insects|night sounds?)\b"}
        kind = next((k for k, rx in kinds.items() if re.search(rx, q)), None)
        sounds = re.search(r"\b(ambience|ambient (sound|noise)s?|background noise|sounds? of|noise of)\b", q)

        def done(intent: str, params: Dict[str, object]) -> EditIntent:
            return EditIntent(intent=intent, target="audio", scope=scope, parameters=params,
                              confidence=0.8, reasoning="keyword match on the music, ambience or levels")

        off = re.search(r"\b(no|mute|silence|turn off|switch off|stop|without|take out|get rid of|remove|delete|drop|kill)\b", q) or \
            re.search(r"\b(turn|switch)\b[^.]*\boff\b|\b(rain|wind|sea|city|night|ambience|sound|sounds)\s+(off|out)\b", q)
        if (kind or sounds) and not music_word:
            if off and not re.search(r"\b(add|put|play|include)\b", q):
                return done("set_ambience", {"ambience": "none", "kind": "none"})
            if kind and (sounds or re.search(r"\b(add|put|play|include|with|some)\b", q)):
                return done("set_ambience", {"ambience": kind, "kind": kind})
            if sounds and not kind:
                return done("set_ambience", {})            # asks which
            # "make the rain quieter", "turn the wind up", "the sea at 50%": the ambience's level (DST-44).
            level = _level_words(q)
            if level is not None:
                return done("set_volume", {"level": "ambience", "target": "ambience", "percent": level})
            return None
        if not music_word:
            # "scene two should feel more tense": a mood for a scene is its music.
            words = set(re.findall(r"[a-z]+", q))
            mood = next((m for m in sorted(music.moods()) if m in words), None) or \
                next((music.ALIASES[w] for w in sorted(words) if w in music.ALIASES), None)
            if re.search(r"\b(feel|sound)\w*\b", q) and mood and scope.startswith("scene:"):
                return done("set_music", {"mood": mood})
            return None
        if re.search(r"\b(mute|silence|turn off|switch off|no music|no more music|without|stop the|kill the)\b", q) or \
                re.search(r"\b(turn|switch)\b[^.]*\boff\b|\b(music|soundtrack|score|bgm|song)\s+(off|out)\b", q):
            return done("set_music", {"off": True})
        percent = _level_words(q)
        if percent is not None:
            return done("set_volume", {"level": "music", "target": "music", "percent": percent})
        lib = music.library()
        by_title = next((t for t in lib.values() if t.title.lower() in q), None)
        if by_title:
            return done("set_music", {"track": by_title.id})
        if re.search(r"\b(back|original|default|as it was)\b", q):
            return done("set_music", {"reset": True})
        words = set(re.findall(r"[a-z]+", q))
        mood = next((m for m in sorted(music.moods()) if m in words), None) or \
            next((music.ALIASES[w] for w in sorted(words) if w in music.ALIASES), None)
        if mood and re.search(r"\b(change|make|switch|use|play|want|should|feel|more|something|to)\b", q) \
                and not re.search(r"\b(add|put|include)\b", q):
            return done("set_music", {"mood": mood})
        return None

    @staticmethod
    def _effect_intent(q: str, scope: str) -> Optional[EditIntent]:
        """"add film grain to scene 2", "make the whole film vignette", "a soft glow", "cinema bars",
        "remove the effect". Named effects only, so 'make scene 2 darker' stays a filter."""
        words = (("grain", r"\b(film )?grain(y)?\b|\b(old|vintage|retro|aged) (film|movie|footage)\b|"
                           r"\bfilm (look|stock)\b|\bfilmic\b|\b(8|16|35) ?mm\b"), ("vignette", r"\bvignett\w*"),
                 ("glow", r"\b(glow|bloom|dreamy glow)\b"), ("letterbox", r"\b(letterbox\w*|cinema bars|cinematic bars|black bars|widescreen bars)\b"),
                 ("glitch", r"\bglitch\w*\b"))
        none = re.search(r"\b(remove|no|without|turn off|take off|drop)\b.*\b(effects?|grain|vignett\w*|glow|bloom|letterbox\w*|bars|glitch\w*)\b", q)
        kind = next((k for k, rx in words if re.search(rx, q)), None)
        if none and re.search(r"\beffects?\b", q) and not kind:
            params: Dict[str, object] = {"effect": "none"}
        elif kind:
            params = {"effect": "none" if none else kind}
            for s in ("soft", "strong"):
                if re.search(rf"\b{s}(ly)?\b|\b{'subtle' if s == 'soft' else 'heavy'}\b", q):
                    params["strength"] = s
        else:
            return None
        return EditIntent(intent="set_effect", target="video", scope=scope, parameters=params,
                          confidence=0.8, reasoning="keyword match on a finishing effect")

    @staticmethod
    def _scene_intent(raw: str, q: str) -> Optional[EditIntent]:
        """"remove scene 2", "move scene 3 to the start", "redraw shot 2 of scene 1",
        'change line 2 of scene 1 to "Hello there"'. The scene is the scope."""
        def done(intent: str, scene: str, params: Dict[str, object]) -> EditIntent:
            return EditIntent(intent=intent, target="video", scope=f"scene:scene_{scene}", parameters=params,
                              confidence=0.82, reasoning="keyword match on a scene or shot change")

        if m := re.search(r"\b(remove|delete|drop|take out|get rid of|cut)\s+(the\s+)?scene\s*(\d+)\b(?!\s+(titles?|names?))", q):
            return done("remove_scene", m.group(3), {})
        if m := re.search(r"\bmove\s+(the\s+)?scene\s*(\d+)\s+(?:to|so (?:it )?is|into)\s+(?:the\s+)?(.+)", q):
            where, scene = m.group(3), m.group(2)
            to = None
            if re.search(r"\b(start|beginning|first|front|top|opening)\b", where):
                to = 1
            elif n := re.search(r"(?:position|place|spot|number|scene)?\s*(\d+)", where):
                to = int(n.group(1))
            if to:
                return done("move_scene", scene, {"to": to})
        if m := re.search(r"\b(redraw|regenerate|redo|remake|draw again)\s+(?:the\s+)?shot\s*(\d+)\s+(?:of|in)\s+scene\s*(\d+)", q) or \
                re.search(r"\b(redraw|regenerate|redo|remake)\s+scene\s*(\d+)\W+shot\s*(\d+)", q):
            a, b = m.group(2), m.group(3)
            # "shot 2 of scene 3" names the shot first; "scene 3, shot 2" names the scene first.
            shot, scene = (a, b) if re.search(r"shot\s*\d+\s+(?:of|in)", m.group(0)) else (b, a)
            return done("regenerate_shot", scene, {"shot": int(shot)})
        quoted = re.search(r"[\"“]([^\"”]{1,300})[\"”]", raw)
        if quoted and (m := re.search(r"\b(?:change|edit|rewrite|replace|make)\s+(?:the\s+)?line\s*(\d+)\s+(?:of|in)\s+scene\s*(\d+)", q)):
            return done("edit_line", m.group(2), {"line": int(m.group(1)), "text": quoted.group(1).strip()})
        return None

    @staticmethod
    def _graphics_intent(raw: str, q: str, scope: str) -> Optional[EditIntent]:
        """"add a title card", "remove the scene titles", "show the character names",
        "add an end card that says Fin". Titles are for the whole film, whatever scope is named."""
        kinds = (("title_card", r"\b(title card|opening title|title screen|main title)\b"),
                 ("scene_titles", r"\b(scene|chapter) (titles?|names?|captions?|headings?)\b"),
                 ("lower_thirds", r"\b(lower[- ]?thirds?|name (bars?|tags?|labels?|captions?)|"
                                  r"character (names|labels|captions)|names? (of|for) (the )?characters)\b"),
                 ("end_card", r"\b(end card|end screen|end credits|closing card|the end card)\b"))
        hit = [(k, rx) for k, rx in kinds if re.search(rx, q)]
        if not hit:
            if re.search(r"\b(no|remove|without|hide|turn off|delete) (all )?(the )?(titles|title graphics|graphics)\b", q):
                return EditIntent(intent="set_graphics", target="video", scope="global",
                                  parameters={"reset": True}, confidence=0.8,
                                  reasoning="keyword match on removing the titles")
            return None
        off = bool(re.search(r"\b(no|remove|without|hide|turn off|take out|delete|drop|get rid of|disable)\b", q))
        params: Dict[str, object] = {k: not off for k, _ in hit}
        quoted = re.search(r"[\"“'‘]([^\"”'’]{1,90})[\"”'’]", raw)
        if quoted and not off and len(hit) == 1:
            if hit[0][0] == "title_card":
                params["title_text"] = quoted.group(1).strip()
            elif hit[0][0] == "end_card":
                params["end_text"] = quoted.group(1).strip()
        return EditIntent(intent="set_graphics", target="video", scope="global", parameters=params,
                          confidence=0.8, reasoning="keyword match on a title graphic")

    @staticmethod
    def _transition_intent(q: str, scenes: List[str]) -> Optional[EditIntent]:
        """"use a slow fade between scenes 1 and 2", "make the cut into shot 3 of scene 2 a wipe",
        "no transition between scenes 2 and 3". Needs the word for a style, or "transition", and a place."""
        mentions = re.search(r"\b(transition|cut|fade|wipe|slide|zoom|dissolve|dip|crossfade|cross-fade|"
                             r"push|pixeli[sz]e|blur|squeeze|reveal|cover|radial)\b", q)
        if not mentions:
            return None
        between = re.search(r"between\s+scenes?\s+(\d+)\s*(?:and|&|to|,)\s*(?:scene\s*)?(\d+)", q)
        inside = re.search(r"\bshot\s*(\d+)\s+(?:of|in)\s+scene\s*(\d+)", q) or \
            re.search(r"\bscene\s*(\d+)\W+shot\s*(\d+)", q)
        into = re.search(r"\binto\s+scene\s*(\d+)", q)
        if not (between or inside or into):
            return None
        params: Dict[str, object] = {}
        none_words = re.search(r"\b(no transition|hard cut|straight cut|just cut|plain cut|a cut)\b", q)
        kind = "cut" if none_words else transitions.from_words(q)
        if kind is None:
            return None
        params["transition"] = kind
        for speed in ("quick", "slow"):
            if re.search(rf"\b{speed}(ly)?\b", q):
                params["speed"] = speed
        if re.search(r"\b(fast|snappy|short)\b", q):
            params["speed"] = "quick"
        ms = re.search(r"(\d+(?:\.\d+)?)\s*(ms|s|sec|seconds?)\b", q)
        if ms:
            value = float(ms.group(1)) * (1 if ms.group(2) == "ms" else 1000)
            params["ms"] = int(value)
        if between:
            scene = int(between.group(2))
        elif inside:
            # "shot 3 of scene 2" or "scene 2, shot 3"
            if re.search(r"shot\s*\d+\s+(?:of|in)", q):
                params["shot"], scene = int(inside.group(1)), int(inside.group(2))
            else:
                scene, params["shot"] = int(inside.group(1)), int(inside.group(2))
        else:
            scene = int(into.group(1))
        return EditIntent(intent="set_transition", target="video", scope=f"scene:scene_{scene}",
                          parameters=params, confidence=0.82,
                          reasoning="keyword match on a transition and a place")

    @staticmethod
    def _extract_params(q: str, kind: str) -> Dict[str, str | float]:
        params: Dict[str, str | float] = {}
        if kind == "tone":
            for k, v in _TONES.items():
                if k in q:
                    params["tone"] = v
                    break
        elif kind in ("filter", "filter_name"):
            for fn in _FILTER_NAMES:
                if re.search(rf"\b{re.escape(fn).replace('_', '[ _]')}\b", q):
                    params["filter"] = fn
                    break
        elif kind == "aesthetic":
            for w in ("dark", "darker", "moody", "gloom", "brighter", "warm", "cool"):
                if w in q:
                    params["aesthetic"] = "darker" if w in ("dark", "moody", "gloom") else w
                    break
        elif kind == "mood":
            for m in _MOODS:
                if m in q:
                    params["mood"] = m
                    break
            params.setdefault("mood", "ambient")
        elif kind == "factor":
            m = re.search(r"(\d+(?:\.\d+)?)\s*x", q)
            params["factor"] = float(m.group(1)) if m else (1.5 if "speed" in q else 0.75)
        elif kind == "volume":
            params["volume"] = 1.3 if "louder" in q else 0.7
        elif kind == "voice":
            params["voice"] = "alternate"
        elif kind == "character":
            params["character_change"] = q
        elif kind == "genre":
            for g in ("comedy", "horror", "drama", "romance", "fantasy", "sci-fi", "scifi", "mystery"):
                if g in q:
                    params["genre"] = g
                    break
        return params


# Convenience function for tests.
def classify(query: str, scenes=None, characters=None, character_names=None,
             scene_titles=None) -> EditIntent:
    return IntentClassifier().classify(query, scenes, characters, character_names, scene_titles)
