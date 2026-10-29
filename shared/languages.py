"""Subtitle languages the pipeline supports, in one place.

Each entry: display name -> (ISO 639-2/B code for MP4 track metadata,
MyMemory locale code for the free translation fallback).
The web UI's language dropdown should only offer names listed here.
"""
from __future__ import annotations
import unicodedata
from collections import Counter
from typing import Dict, List, Optional, Tuple

SUBTITLE_LANGUAGES: Dict[str, Tuple[str, str]] = {
    "English":    ("eng", "en-GB"),
    "Urdu":       ("urd", "ur-PK"),
    "Hindi":      ("hin", "hi-IN"),
    "Arabic":     ("ara", "ar-SA"),
    "French":     ("fre", "fr-FR"),
    "Spanish":    ("spa", "es-ES"),
    "German":     ("ger", "de-DE"),
    "Italian":    ("ita", "it-IT"),
    "Portuguese": ("por", "pt-PT"),
    "Russian":    ("rus", "ru-RU"),
    "Turkish":    ("tur", "tr-TR"),
    "Japanese":   ("jpn", "ja-JP"),
    "Korean":     ("kor", "ko-KR"),
    "Chinese":    ("chi", "zh-CN"),
}


def canonical(name: Optional[str]) -> Optional[str]:
    """Case-insensitive lookup -> canonical display name, or None if unsupported."""
    if not name:
        return None
    key = name.strip().lower()
    return next((n for n in SUBTITLE_LANGUAGES if n.lower() == key), None)


# Scripts that need a font with Arabic/Indic/CJK coverage when burned into video,
# and (for Arabic script) right-to-left shaping.
RTL_LANGUAGES = {"Urdu", "Arabic"}
WIDE_SCRIPT_LANGUAGES = RTL_LANGUAGES | {"Hindi", "Japanese", "Korean", "Chinese"}


def iso639_1(name: str) -> str:
    """Two-letter code, used for sidecar filenames like `film.ur.srt`."""
    lang = canonical(name)
    return SUBTITLE_LANGUAGES[lang][1].split("-")[0] if lang else "und"


def is_rtl(name: str) -> bool:
    return canonical(name) in RTL_LANGUAGES


def iso639_2(name: str) -> str:
    lang = canonical(name)
    return SUBTITLE_LANGUAGES[lang][0] if lang else "und"


def mymemory_code(name: str) -> Optional[str]:
    lang = canonical(name)
    return SUBTITLE_LANGUAGES[lang][1] if lang else None


def supported_names() -> List[str]:
    return list(SUBTITLE_LANGUAGES)


# ---- spoken dialogue (DST-17) ---------------------------------------------------
# Kokoro reads every line as English (`lang="en-us"`), so a line written in another
# writing system comes out as minutes of letter-by-letter noise: Urdu dialogue made
# a 30 s film 150.8 s long in the DST-10 baseline. A line's writing system says its
# language; these are voiced by Edge TTS instead (or gTTS, its fallback).
# Language -> (gTTS code, Edge male voice, Edge female voice). Every voice was
# checked against edge-tts 7.3.1's voice list and every code against gTTS 2.5.4's
# languages on 2026-10-29.
SPEECH_VOICES: Dict[str, Tuple[str, str, str]] = {
    "Urdu":     ("ur", "ur-PK-AsadNeural", "ur-PK-UzmaNeural"),
    "Arabic":   ("ar", "ar-SA-HamedNeural", "ar-SA-ZariyahNeural"),
    "Hindi":    ("hi", "hi-IN-MadhurNeural", "hi-IN-SwaraNeural"),
    "Japanese": ("ja", "ja-JP-KeitaNeural", "ja-JP-NanamiNeural"),
    "Korean":   ("ko", "ko-KR-InJoonNeural", "ko-KR-SunHiNeural"),
    "Chinese":  ("zh-CN", "zh-CN-YunxiNeural", "zh-CN-XiaoxiaoNeural"),
    "Russian":  ("ru", "ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural"),
}
# Letters Urdu writes and Arabic does not: tteh, ddal, rreh, noon ghunna, barree
# yeh (and its hamza form), do-chashmee heh and heh goal.
_URDU_ONLY = set("ٹڈڑںےۓھہ")
# First word of a letter's Unicode name -> the language that script stands for.
_SCRIPTS = {"ARABIC": "Arabic", "DEVANAGARI": "Hindi", "HIRAGANA": "Japanese",
            "KATAKANA": "Japanese", "HANGUL": "Korean", "CJK": "Chinese",
            "CYRILLIC": "Russian"}


def spoken_language(text: str) -> Optional[str]:
    """The language `text` is written in, when its writing system decides it.

    None for Latin script (English, but also Spanish or Roman Urdu, which cannot
    be told apart by their letters) and for text with no letters: those stay with
    whichever engine the voice chain picks.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    scripts = Counter(_SCRIPTS.get(unicodedata.name(c, "").split(" ")[0], "Latin")
                      for c in letters)
    language, count = scripts.most_common(1)[0]
    if language == "Latin" or count * 2 < len(letters):
        return None
    if language == "Chinese" and scripts["Japanese"]:
        return "Japanese"          # Japanese mixes kanji with kana; Chinese has no kana
    if language == "Arabic" and any(c in _URDU_ONLY for c in letters):
        return "Urdu"
    return language
