"""DST-9: the labelled test set and the benchmark mode that runs it."""
from __future__ import annotations
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TESTSET = ROOT / "eval" / "testset.jsonl"


@pytest.fixture
def bench():
    spec = importlib.util.spec_from_file_location("benchmark", ROOT / "scripts" / "benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case(**overrides):
    case = {"id": "t-01", "prompt": "A robot learns to paint", "target_duration_s": 20,
            "expected_scenes": 2, "expected_characters": 1, "subtitle_language": "English",
            "tags": ["genre:sci-fi", "tone:joyful"], "render_subset": False}
    case.update(overrides)
    return case


def _write(path: Path, *cases) -> Path:
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
                    encoding="utf-8")
    return path


# ---- the data file ----------------------------------------------------------

def test_the_testset_is_well_formed(bench):
    cases = bench.load_testset(TESTSET)           # the loader's own checks pass
    assert len(cases) == 25
    assert all(re.fullmatch(r"dst-\d\d", c["id"]) for c in cases)

    for c in cases:
        assert c["target_duration_s"] in (20, 30, 45, 60)       # what the interface offers
        assert 2 <= c["expected_scenes"] <= 6
        chars = c["expected_characters"]
        assert chars is None or (isinstance(chars, int) and not isinstance(chars, bool)
                                 and chars >= 0)
        assert isinstance(c["render_subset"], bool)
        genres = [t for t in c["tags"] if t.startswith("genre:")]
        tones = [t for t in c["tags"] if t.startswith("tone:")]
        assert len(genres) == 1 and len(tones) == 1, c["id"]
        assert tones[0] in {"tone:neutral", "tone:tense", "tone:joyful",
                            "tone:melancholic", "tone:unspecified"}, c["id"]
        assert all(t.split(":")[0] in ("genre", "tone", "edge") for t in c["tags"]), c["id"]


def test_the_testset_covers_what_the_ticket_asks_for(bench):
    cases = bench.load_testset(TESTSET)
    languages = {c["subtitle_language"] for c in cases}
    assert len(languages) >= 3 and "Urdu" in languages
    edges = {t for c in cases for t in c["tags"] if t.startswith("edge:")}
    assert {"edge:short-prompt", "edge:long-prompt", "edge:non-english-prompt",
            "edge:safety-filter-risk"} <= edges
    assert len(bench.load_testset(TESTSET, render_subset=True)) == 8


# ---- the loader ---------------------------------------------------------------

@pytest.mark.parametrize("line,message", [
    ("{not json", "not valid JSON"),
    (json.dumps({k: v for k, v in _case().items() if k != "tags"}), "missing tags"),
    (json.dumps(_case(prompt="   ")), "prompt is empty"),
    (json.dumps(_case(expected_scenes=0)), "expected_scenes must be"),
    (json.dumps(_case(target_duration_s=True)), "target_duration_s must be"),
    (json.dumps(_case(target_duration_s="30")), "target_duration_s must be"),
    (json.dumps(_case(subtitle_language="Klingon")), "Klingon"),
    (json.dumps(_case(subtitle_language="urdu")), "urdu"),      # labels use the exact name
])
def test_a_bad_line_is_refused_with_its_line_number(bench, tmp_path, line, message):
    path = tmp_path / "set.jsonl"
    path.write_text(json.dumps(_case(id="t-00")) + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2") as error:
        bench.load_testset(path)
    assert message in str(error.value)


def test_duplicate_and_unknown_ids_are_refused(bench, tmp_path):
    with pytest.raises(ValueError, match="duplicate id"):
        bench.load_testset(_write(tmp_path / "dup.jsonl", _case(), _case()))
    path = _write(tmp_path / "set.jsonl", _case())
    with pytest.raises(ValueError, match="t-99"):
        bench.load_testset(path, ids=["t-01", "t-99"])


def test_the_loader_narrows_by_id_and_by_subset(bench, tmp_path):
    path = _write(tmp_path / "set.jsonl", _case(id="a"), _case(id="b", render_subset=True),
                  _case(id="c", render_subset=True))
    assert [c["id"] for c in bench.load_testset(path, ids=["c", "a"])] == ["a", "c"]
    assert [c["id"] for c in bench.load_testset(path, render_subset=True)] == ["b", "c"]
    assert [c["id"] for c in bench.load_testset(path, ids=["a", "b"], render_subset=True)] == ["b"]


def test_testset_only_options_need_a_testset(bench, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["benchmark.py", "--render-subset"])
    with pytest.raises(SystemExit):
        bench.main()


# ---- what a run records ---------------------------------------------------------

def test_a_failed_llm_call_records_its_provider_but_not_its_message(bench, monkeypatch):
    from mcp.tools.llm_tools.llm_client import LLMClient
    from shared.providers import ProviderSpec

    def refuse(self, spec, *args, **kwargs):
        raise RuntimeError("quota exceeded for key sk-do-not-keep")

    monkeypatch.setattr(LLMClient, "_call", refuse)
    with bench._watch_calls() as calls:
        with pytest.raises(RuntimeError):
            LLMClient("story")._call(ProviderSpec(role="story", provider="groq", model="m"),
                                     "prompt", "", 0.5, 10)
    assert calls["llm"] == [{"role": "story", "provider": "groq", "model": "m",
                             "ok": False, "error": "RuntimeError"}]
    assert LLMClient._call is refuse                 # the real method is put back afterwards


def test_a_provider_forced_for_a_role_is_named_in_the_report(bench, monkeypatch):
    monkeypatch.setenv("PROVIDER_STORY", "groq")
    monkeypatch.delenv("PROVIDER_TTS", raising=False)
    forced = bench._forced_providers()
    assert forced["story"] == "groq"
    assert "tts" not in forced                       # a role left alone is not listed


def test_errors_never_quote_a_credential(bench, monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "sk-test-1234567890")
    monkeypatch.setenv("SHORT_TOKEN", "abc")         # too short to replace safely
    assert bench._redact("401 for sk-test-1234567890 (abc)") == "401 for *** (abc)"


def test_a_testset_run_plans_every_prompt_and_renders_the_subset(
        bench, tmp_path, monkeypatch, isolated_dirs):
    """Offline, end to end: the report holds what DST-10 needs to score each prompt."""
    path = _write(tmp_path / "set.jsonl",
                  _case(id="plan-only", expected_scenes=3),
                  _case(id="rendered", subtitle_language="Urdu", render_subset=True))
    monkeypatch.setattr(bench, "ROOT", tmp_path)
    monkeypatch.setattr(bench, "_offline", lambda: None)  # conftest already forces offline
    monkeypatch.setenv("PROVIDER_TTS", "silent")
    monkeypatch.setattr(sys, "argv", ["benchmark.py", "--offline", "--testset", str(path),
                                      "--no-subs", "--no-bgm"])
    assert bench.main() == 0

    report = json.loads(next((tmp_path / "data" / "benchmarks").glob("*_testset.json"))
                        .read_text(encoding="utf-8"))
    assert report["commit"]
    assert report["forced_providers"]["image"] == "placeholder"   # conftest pins it
    planned, rendered = report["runs"]

    assert planned["id"] == "plan-only" and planned["render"] is None
    assert planned["scenes"] == 3                       # the scene count asked for
    assert planned["script_source"] == "template"       # offline: no model wrote it
    assert planned["characters"] == len(planned["character_names"]) >= 1
    assert [i["provider"] for i in planned["storyboard_images"]] == ["placeholder"] * 3

    film = rendered["render"]
    assert rendered["subtitle_language"] == "Urdu"
    assert film["in_sync"] and film["lines_on_a_cut"] == film["lines"]
    assert film["render_s"] > 0 and film["total_s"] >= film["render_s"]
    assert {v["engine"] for v in film["voices"]} == {"silent"}
    # Every image in the film is accounted for: one preview per scene plus the render's own.
    assert sum(film["image_providers"].values()) == (
        len(rendered["storyboard_images"]) + len(film["images"]))


def test_one_failing_prompt_does_not_stop_the_rest(bench, tmp_path, monkeypatch, isolated_dirs):
    path = _write(tmp_path / "set.jsonl", _case(id="breaks"), _case(id="fine"))
    real = bench.run_case

    def run_case(case, args):
        if case["id"] == "breaks":
            raise RuntimeError("ffmpeg fell over")
        return real(case, args)

    monkeypatch.setattr(bench, "run_case", run_case)
    monkeypatch.setattr(bench, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["benchmark.py", "--testset", str(path)])
    assert bench.main() == 1                            # a failure is still reported

    report = json.loads(next((tmp_path / "data" / "benchmarks").glob("*_testset.json"))
                        .read_text(encoding="utf-8"))
    broken, fine = report["runs"]
    assert broken["error"] == "RuntimeError: ffmpeg fell over"
    assert "error" not in fine and fine["scenes"] == 2
