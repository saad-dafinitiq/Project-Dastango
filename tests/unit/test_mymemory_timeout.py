"""DST-18: MyMemory, the last step of the translate chain, has a time limit too.

The hang test uses the same real socket as the DST-12 tests: a server that
accepts the connection and never answers.
"""
from __future__ import annotations
import contextvars

import pytest

from mcp.tools.llm_tools import translate_tool
from mcp.tools.llm_tools.translate_tool import TranslateTool
from shared import tracing
from tests.unit.test_model_call_timeouts import SLACK_S, _within, chain, silent  # noqa: F401
from tests.unit.test_model_call_tracing import spans  # noqa: F401


class FakeResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def test_a_hung_mymemory_gives_up_within_its_limit(chain, silent, monkeypatch):  # noqa: F811
    monkeypatch.setattr(translate_tool, "_MYMEMORY_URL", silent.url)
    chain("""
        roles:
          translate:
            - {provider: mymemory, timeout_s: 1}
    """)
    result, elapsed = _within(20, lambda: TranslateTool().run(
        lines=["Hello there."], target_language="French"))

    assert result.success is False                     # the caller skips the language
    assert "mymemory: no answer within 1 s" in result.error
    assert silent.connections == 1                     # a timeout is not retried
    assert 1.0 <= elapsed < 1.0 + SLACK_S


@pytest.mark.parametrize("entry,expected", [
    ("{provider: mymemory}", 60),                      # the translate role's default
    ("{provider: mymemory, timeout_s: 7}", 7),
])
def test_mymemory_takes_its_limit_from_the_provider_config(chain, monkeypatch,  # noqa: F811
                                                           entry, expected):
    seen = {}

    def recording_get(url, params=None, timeout=None, **_):
        seen.update(url=url, params=params, timeout=timeout)
        return FakeResponse(200, {"responseData": {"translatedText": "Bonjour."}})

    monkeypatch.setattr(translate_tool.requests, "get", recording_get)
    chain(f"roles:\n  translate:\n    - {entry}\n")
    result = TranslateTool().run(lines=["Hello."], target_language="French")

    assert result.success and result.data == ["Bonjour."]
    assert result.metadata["provider"] == "mymemory"
    assert seen["timeout"] == expected
    assert seen["url"].startswith("https://")
    assert seen["params"]["langpair"].startswith("en-GB|")


def test_a_refusal_is_still_retried_and_reported(chain, monkeypatch):  # noqa: F811
    """Only a timeout skips the retries; a 503 gets the three attempts it always had."""
    calls = []

    def refusing_get(url, params=None, timeout=None, **_):
        calls.append(1)
        return FakeResponse(503, {})

    monkeypatch.setattr(translate_tool.requests, "get", refusing_get)
    monkeypatch.setattr(translate_tool.time, "sleep", lambda _s: None)
    chain("roles:\n  translate:\n    - {provider: mymemory}\n")
    result = TranslateTool().run(lines=["Hello."], target_language="French")

    assert result.success is False and "HTTP 503" in result.error
    assert len(calls) == 3


def test_an_over_long_line_is_refused_before_it_is_sent(monkeypatch):
    """As deep_translator did: MyMemory would answer with its error text instead."""
    monkeypatch.setattr(translate_tool.requests, "get",
                        lambda *a, **k: pytest.fail("an over-long query was sent"))
    with pytest.raises(ValueError, match="500-character limit"):
        translate_tool._mymemory_request("x" * 501, "fr-FR", 60)


def test_a_mymemory_timeout_is_a_failed_call_in_the_trace(spans, chain, silent,  # noqa: F811
                                                         monkeypatch):
    monkeypatch.setattr(translate_tool, "_MYMEMORY_URL", silent.url)
    chain("roles:\n  translate:\n    - {provider: mymemory, timeout_s: 1}\n")
    with tracing.job_trace("job-1", "project-1", "render"):
        # _within runs the call on its own thread; copy the job's context into
        # it, as run_jobs does for the pipeline's worker threads.
        context = contextvars.copy_context()
        _within(20, lambda: context.run(TranslateTool().run, lines=["Hello there."],
                                        target_language="French"))

    assert len(spans.spans) == 1
    span = spans.spans[0]
    assert {k: span[k] for k in ("kind", "role", "provider", "attempt", "ok", "timed_out",
                                 "job_id")} == {
        "kind": "translate", "role": "translate", "provider": "mymemory", "attempt": 1,
        "ok": False, "timed_out": True, "job_id": "job-1"}
    # Sizes only, never the subtitle text.
    assert spans.io[0]["input"] == {"text_chars": len("Hello there.")}


def _orchestrator(isolated_dirs):
    from agents.orchestrator import PipelineOrchestrator
    from state_manager.state_manager import StateManager
    from state_manager.storage import SqliteStorage
    from tests.conftest import silence_tts

    orch = PipelineOrchestrator(state_manager=StateManager(SqliteStorage(isolated_dirs / "db")))
    silence_tts(orch.audio.tools)
    return orch


def _film(orch):
    events = []
    state = orch.run_full("A kite flies over a quiet town", on_event=events.append,
                          target_duration_s=20, scene_count=2, with_bgm=False,
                          with_subtitles=True, subtitle_language="French",
                          width=320, height=180, fps=12)
    return state, events[-1]


def test_a_skipped_language_is_named_on_the_completion_event(isolated_dirs, monkeypatch):
    def timed_out(self, lines, lang):
        raise RuntimeError("no answer within 60 s")

    monkeypatch.setattr(TranslateTool, "_mymemory", timed_out)
    state, done = _film(_orchestrator(isolated_dirs))

    # The film is still finished, with the English track, and says what it lacks.
    assert (done.phase, done.status) == ("complete", "complete")
    assert state.video.subtitle_languages == ["English"]
    assert state.video.subtitles_skipped == ["French"]
    assert done.payload["subtitles_skipped"] == ["French"]
    assert "subtitles skipped, translation failed: French" in done.message


def test_nothing_is_reported_when_every_language_translates(isolated_dirs, fake_translation):
    state, done = _film(_orchestrator(isolated_dirs))

    assert state.video.subtitles_skipped == []
    assert "subtitles_skipped" not in done.payload
    assert "skipped" not in done.message
