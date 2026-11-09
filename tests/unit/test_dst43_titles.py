"""DST-43 (L2): the title card gives way to the first subtitle, the rule clears a title of two lines,
typographic punctuation keeps the chosen face, a stripped escape leaves no debris, the end card does
not dim the last subtitle."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from shared import graphics
from tests.unit.test_dst39_graphics import SIZES, _events, _film

BS = chr(92)                                  # a backslash, the start of every ASS code
POS = re.compile(re.escape(BS + "pos(") + r"([\d.]+),([\d.]+)\)")
FS = re.compile(re.escape(BS + "fs") + r"(\d+)")


@pytest.mark.parametrize("raw, clean", [
    ("Hello {" + BS + "fs250" + BS + "c&H0000FF&}BIG " + BS + "N Line2 " + BS + "h ok", "Hello BIG Line2 ok"),
    ("a" + BS + "Nb", "a b"),
    ("back" + BS + "slash and {braces}", "backslash and"),
    ("plain title", "plain title"),
])
def test_a_title_keeps_its_words_and_loses_only_the_codes(raw, clean):
    assert graphics._clean(raw) == clean


@pytest.mark.parametrize("text", ["Wait… what", "One night — two bakers", "“Quoted” title", "It’s 100 %", "a ♥ b"])
def test_punctuation_and_symbols_keep_the_chosen_face(text):
    assert graphics._font(text, graphics.TITLE_FONT, "English") == graphics.TITLE_FONT


@pytest.mark.parametrize("text", ["کہانی", "कहानी", "物語", "Привет"])
def test_letters_from_another_script_still_get_a_face_that_has_them(text):
    assert graphics._font(text, graphics.TITLE_FONT, "Urdu") != graphics.TITLE_FONT


def _card(ass: str):
    return [e for e in _events(ass) if "Rival" in e[2]][0]


def _line(ass: str, key: str) -> str:
    return [ln for ln in ass.splitlines() if key in ln][0]


def test_the_title_card_ends_when_the_first_burned_subtitle_begins():
    script, manifest = _film()
    manifest.segments[:] = [s.model_copy(update={"start_ms": s.start_ms + 1400, "end_ms": s.end_ms + 1400})
                            if s.kind == "dialogue" else s for s in manifest.segments]
    script.story.graphics.title_text = "Rival Bakers"
    first = min(s.start_ms for s in manifest.segments if s.kind == "dialogue") / 1000          # 2.0 s
    with_subs = _card(graphics.build_ass(script, manifest, 1920, 1080, subtitles=True))
    without = _card(graphics.build_ass(script, manifest, 1920, 1080, subtitles=False))
    assert with_subs[1] <= first, "the card must be gone when the first subtitle appears"
    assert without[1] > first, "with no subtitles burned the card keeps its full time"


def test_the_card_never_shrinks_below_a_readable_time():
    script, manifest = _film()
    script.story.graphics.title_text = "Rival Bakers"
    # the first line starts at 0.6 s: the card still gets about a second
    got = _card(graphics.build_ass(script, manifest, 1920, 1080, subtitles=True))
    assert got[1] - got[0] >= 0.7


@pytest.mark.parametrize("fmt", ["16:9", "9:16", "1:1"])
def test_the_rule_and_the_tagline_sit_below_every_line_of_the_title(fmt):
    w, h = SIZES[fmt]
    script, manifest = _film(fmt)
    script.story.graphics.title_text = "Rival Bakers Swap Shops Every Single Night"
    script.story.logline = "Two rival bakers swap shops for one night and everything rises or burns"
    ass = graphics.build_ass(script, manifest, w, h)
    title = _line(ass, "Rival")
    lines = title.split(BS + "N")
    top = float(POS.search(title).group(2))
    size = int(FS.search(title).group(1))
    rule = float(POS.search(_line(ass, BS + "fscx0")).group(2))
    tagline = float(POS.search(_line(ass, "Two rival")).group(2))
    assert rule > top + len(lines) * size * 1.15, "the rule is drawn through the title"
    assert tagline > rule


def test_a_title_too_wide_for_a_tall_film_is_wrapped_by_us_not_left_to_the_renderer():
    script, manifest = _film("9:16")
    script.story.graphics.title_text = "Rival Bakers Swap Shops Every Single Night"
    ass = graphics.build_ass(script, manifest, 1080, 1920)
    assert BS + "N" in _line(ass, "Rival")


def test_the_rule_is_centred_on_the_frame():
    script, manifest = _film("16:9")
    ass = graphics.build_ass(script, manifest, 1920, 1080)
    rule = _line(ass, BS + "fscx0")
    assert float(POS.search(rule).group(1)) == 960 and "m 0 0 l" in rule     # drawn from its own corner


def test_the_titles_go_on_under_the_subtitles(monkeypatch):
    """One pass, titles first: the end card's scrim must not dim the last subtitle."""
    from mcp.tools.video_tools import subtitle_tool

    seen = []
    monkeypatch.setattr(subtitle_tool.subprocess, "run",
                        lambda cmd, *a, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    d = Path(tempfile.mkdtemp())
    (d / "g.ass").write_text("[Script Info]\n", encoding="utf-8")
    subtitle_tool.SubtitleTool().run(in_path=str(d / "in.mp4"), out_path=str(d / "out.mp4"),
                                     lines=[{"start_ms": 0, "end_ms": 900, "text": "hi"}],
                                     graphics_ass=str(d / "g.ass"))
    vf = seen[0][seen[0].index("-vf") + 1]
    assert vf.index("ass=") < vf.index("subtitles="), "the subtitles must be drawn after the titles"
