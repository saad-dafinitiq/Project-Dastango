"""DST-11: every model call emits one span, grouped by job, with no secrets in it."""
from __future__ import annotations
import logging
import socket
import textwrap

import pytest

import jobs
from jobs import worker
from shared import providers, tracing
from shared.utils.parallel import run_jobs
# Real local servers from the DST-12 tests: one answers like an OpenAI-compatible API.
from tests.unit.test_model_call_timeouts import ChatServer


class FakeTracer:
    """Records what would have been sent."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.spans: list = []        # metadata of each finished call
        self.io: list = []           # (input, output) of each finished call
        self.jobs: list = []         # (job, input, output) of each finished job
        self.flushes = 0
        self._inputs: dict = {}

    def _check(self):
        if self.fail:
            raise RuntimeError("trace server down")

    def start_job(self, job, input):
        self._check()
        handle = type("Root", (), {"id": f"root-{job['job_id']}"})()
        self._inputs[id(handle)] = input
        return handle

    def end_job(self, handle, job, output):
        self._check()
        self.jobs.append((dict(job), self._inputs.pop(id(handle), None), output))

    def start(self, fields, input, job):
        self._check()
        handle = object()
        self._inputs[id(handle)] = (input, job.get("root_id"))
        return handle

    def end(self, handle, fields, output):
        self._check()
        input, parent = self._inputs.pop(id(handle))
        self.spans.append(fields)
        self.io.append({"input": input, "output": output, "parent": parent})

    def flush(self):
        if self.fail:
            raise RuntimeError("trace server down")
        self.flushes += 1


@pytest.fixture
def spans():
    fake = FakeTracer()
    tracing.use(fake)
    yield fake
    tracing.use(None)            # the next caller reads the environment again


@pytest.fixture
def chain(tmp_path, monkeypatch):
    from mcp.tools.llm_tools.llm_client import reset_clients
    for name in ("LLM_PROVIDER", "PROVIDER_STORY"):
        monkeypatch.delenv(name, raising=False)

    def use(yaml_text: str):
        path = tmp_path / "providers.yaml"
        path.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
        monkeypatch.setenv("PROVIDERS_FILE", str(path))
        reset_clients()
        providers.load(force=True)

    yield use
    monkeypatch.delenv("PROVIDERS_FILE", raising=False)
    reset_clients()
    providers.load(force=True)


@pytest.fixture
def answering():
    server = ChatServer(reply='{"word": "hello"}')
    yield server
    server.close()


def _refused_url() -> str:
    """An address where nothing listens, so a call fails at once."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


# ---- one call, one span -------------------------------------------------------

def test_an_llm_call_emits_one_span_with_the_right_fields(spans, chain, answering, monkeypatch):
    from mcp.tools.llm_tools.llm_client import LLMClient

    monkeypatch.setenv("OLLAMA_HOST", answering.url)
    chain("""
        roles:
          story:
            - {provider: ollama, model: qwen-test, requires: [OLLAMA_HOST]}
            - {provider: mock}
    """)
    with tracing.job_trace("job-1", "project-1", "plan"):
        assert LLMClient("story").generate("hi").provider == "ollama"

    assert len(spans.spans) == 1
    span = spans.spans[0]
    assert {k: span[k] for k in span if k != "latency_ms"} == {
        "kind": "llm", "role": "story", "provider": "ollama", "model": "qwen-test",
        "position": 1, "fallback": False, "attempt": 1, "ok": True, "timed_out": False,
        # What the server reported in `usage`, read by the real OpenAI SDK.
        "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
        "job_id": "job-1", "job_kind": "plan", "project_id": "project-1",
    }
    assert isinstance(span["latency_ms"], int) and span["latency_ms"] >= 0
    assert spans.flushes == 1                       # sent when the job ended
    # Input and output are sizes, never text, and the call sits under the job's root.
    assert spans.io == [{"input": {"prompt_chars": 2, "system_chars": 0, "max_tokens": 2000},
                         "output": {"answer_chars": len('{"word": "hello"}')},
                         "parent": "root-job-1"}]


def test_a_fallback_shows_as_a_failed_span_then_a_fallback_span(
        spans, chain, answering, monkeypatch):
    from mcp.tools.llm_tools.llm_client import LLMClient

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", _refused_url())
    monkeypatch.setenv("OLLAMA_HOST", answering.url)
    chain("""
        roles:
          story:
            - {provider: gemini, model: gemini-flash-latest, requires: [GEMINI_API_KEY]}
            - {provider: ollama, model: qwen-test, requires: [OLLAMA_HOST]}
    """)
    assert LLMClient("story").generate("hi").provider == "ollama"

    gemini, ollama = spans.spans
    assert (gemini["provider"], gemini["ok"], gemini["fallback"], gemini["position"]) == \
        ("gemini", False, False, 1)
    assert gemini["error"] and gemini["timed_out"] is False
    assert (ollama["provider"], ollama["ok"], ollama["fallback"], ollama["position"]) == \
        ("ollama", True, True, 2)


def test_an_answer_that_is_not_json_is_a_failed_attempt(spans, chain, monkeypatch):
    from pydantic import BaseModel
    from mcp.tools.llm_tools.llm_client import LLMClient

    class Word(BaseModel):
        word: str

    server = ChatServer(reply="sorry, no JSON today")
    try:
        monkeypatch.setenv("OLLAMA_HOST", server.url)
        chain("""
            roles:
              story: [{provider: ollama, model: qwen-test, requires: [OLLAMA_HOST]}]
        """)
        with pytest.raises(RuntimeError):
            LLMClient("story").generate_structured("hi", Word)
    finally:
        server.close()
    assert [(s["attempt"], s["ok"], s["error"]) for s in spans.spans] == \
        [(1, False, "JSONDecodeError"), (2, False, "JSONDecodeError")]


def test_image_and_voice_calls_are_traced_too(spans, tmp_path):
    from mcp.tools.audio_tools.tts_tool import TtsTool
    from mcp.tools.vision_tools.image_gen_tool import ImageGenTool

    ImageGenTool().run(prompt="a lighthouse", out_path=str(tmp_path / "a.png"),
                       width=64, height=36)             # conftest pins placeholder images
    TtsTool().run(text="Hello.", out_path=str(tmp_path / "a.wav"), engine="silent")

    image, voice = spans.spans
    assert (image["kind"], image["role"], image["provider"], image["ok"]) == \
        ("image", "image", "placeholder", True)
    assert (voice["kind"], voice["provider"], voice["ok"], voice["fallback"]) == \
        ("tts", "silent", True, False)


def test_a_voice_that_falls_back_says_so(spans, monkeypatch, tmp_path):
    from mcp.tools.audio_tools import tts_tool

    def kokoro_missing(self, *args, **kwargs):
        raise RuntimeError("Kokoro model files not found")

    def edge_offline(self, text, out, *args, **kwargs):
        out = out.with_suffix(".mp3")
        out.write_bytes(b"ID3")
        return out

    monkeypatch.setattr(tts_tool.TtsTool, "_kokoro", kokoro_missing)
    monkeypatch.setattr(tts_tool.TtsTool, "_edge_tts", edge_offline)
    tts_tool.TtsTool().run(text="Hi.", out_path=str(tmp_path / "a.wav"), engine="kokoro")

    assert [(s["provider"], s["ok"], s["fallback"]) for s in spans.spans] == \
        [("kokoro", False, False), ("edge", True, True)]


# ---- grouping ---------------------------------------------------------------------

def test_calls_in_worker_threads_carry_the_job(spans, tmp_path):
    """Images and voices run in run_jobs' thread pool; the job id must follow them."""
    from mcp.tools.vision_tools.image_gen_tool import ImageGenTool

    def draw(n):
        return ImageGenTool().run(prompt=f"shot {n}", out_path=str(tmp_path / f"{n}.png"),
                                  width=64, height=36)

    with tracing.job_trace("job-7", "project-7", "render"):
        run_jobs([(draw, (n,)) for n in range(4)], workers=4, label="test")
    draw(99)                                          # after the job: no job at all

    inside, after = spans.spans[:4], spans.spans[4]
    assert {(s["job_id"], s["project_id"]) for s in inside} == {("job-7", "project-7")}
    assert "job_id" not in after and "project_id" not in after


def test_the_worker_traces_a_job_under_its_own_id(spans, isolated_dirs, tmp_path):
    from mcp.tools.vision_tools.image_gen_tool import ImageGenTool

    class Orchestrator:
        def plan(self, project_id=None, on_event=None, **payload):
            ImageGenTool().run(prompt="preview", out_path=str(tmp_path / "p.png"),
                               width=64, height=36)

    job = jobs.enqueue("plan", "project-9", {"prompt": "a lighthouse", "scene_count": 3})
    assert worker.run_job(job, orchestrator=Orchestrator()) == "succeeded"
    (span,) = spans.spans
    assert (span["job_id"], span["project_id"], span["job_kind"]) == (job.id, "project-9", "plan")
    (traced_job, job_input, _), = spans.jobs
    assert traced_job["job_id"] == job.id and traced_job["job_kind"] == "plan"
    # The job's settings, with the creator's prompt reduced to its length.
    assert job_input == {"scene_count": 3, "prompt_chars": len("a lighthouse")}


def test_a_job_root_records_its_outcome_and_who_owns_it(spans):
    from types import SimpleNamespace
    state = SimpleNamespace(stage="rendered", version=4,
                            script=SimpleNamespace(scenes=[1, 2, 3]),
                            audio=SimpleNamespace(manifest=SimpleNamespace(total_duration_ms=27140)))
    with tracing.job_trace("job-2", "project-2", "edit", user_id="user-abc",
                           settings={"query": "make scene 2 darker", "user_id": "user-abc"}) as t:
        t.finish(tracing.summarize(state))

    (job, job_input, output), = spans.jobs
    assert job["user_id"] == "user-abc"
    assert job_input == {"request_chars": len("make scene 2 darker")}   # never the words
    assert output == {"stage": "rendered", "version": 4, "scenes": 3, "film_s": 27.1}


def test_a_failed_job_is_recorded_as_failed(spans):
    with pytest.raises(RuntimeError):
        with tracing.job_trace("job-3", "project-3", "render"):
            raise RuntimeError("ffmpeg fell over")
    (job, _, _), = spans.jobs
    assert (job["ok"], job["error"]) == (False, "RuntimeError")


def test_names_are_stable_verb_first_and_never_carry_ids_or_models():
    """Langfuse best practice: names are an API for dashboards; ids go in attributes."""
    assert [tracing.operation_name(r) for r in ("story", "edit_intent", "translate",
                                                "image", "tts")] == \
        ["write-script", "classify-edit", "translate-subtitles", "generate-image",
         "synthesize-voice"]
    assert [tracing.trace_name(k) for k in ("plan", "render", "edit", "revert",
                                            "run_full", "rerun_phase")] == \
        ["plan-storyboard", "render-film", "edit-film", "revert-version", "make-film",
         "rerun-phase"]


# ---- safety -----------------------------------------------------------------------

def test_no_prompt_or_credential_ever_reaches_a_span(spans, chain, answering, monkeypatch):
    from mcp.tools.llm_tools.llm_client import LLMClient

    secret = "sk-live-0123456789abcdef"
    monkeypatch.setenv("SOME_PROVIDER_API_KEY", secret)
    monkeypatch.setenv("OLLAMA_HOST", answering.url)
    # A model name that somehow contains a key: the second line of defence.
    chain(f"""
        roles:
          story: [{{provider: ollama, model: "m-{secret}", requires: [OLLAMA_HOST]}}]
    """)
    LLMClient("story").generate("PRIVATE STORY TEXT about Zara", system="SYSTEM PROMPT")

    (span,) = spans.spans
    assert set(span) <= set(tracing.SPAN_FIELDS)
    values = " ".join(str(v) for v in span.values())
    assert secret not in values and "***" in span["model"]
    assert "PRIVATE STORY TEXT" not in values and "SYSTEM PROMPT" not in values
    assert "hello" not in values                     # nor the model's answer


def test_a_broken_trace_server_never_breaks_a_film(chain, answering, monkeypatch, tmp_path):
    from mcp.tools.llm_tools.llm_client import LLMClient
    from mcp.tools.vision_tools.image_gen_tool import ImageGenTool

    tracing.use(FakeTracer(fail=True))
    try:
        monkeypatch.setenv("OLLAMA_HOST", answering.url)
        chain("""
            roles:
              story: [{provider: ollama, model: qwen-test, requires: [OLLAMA_HOST]}]
        """)
        with tracing.job_trace("job-1", "project-1", "plan"):     # flush fails too
            assert LLMClient("story").generate("hi").provider == "ollama"
            assert ImageGenTool().run(prompt="x", out_path=str(tmp_path / "x.png"),
                                      width=64, height=36).success
    finally:
        tracing.use(None)


# ---- configuration ------------------------------------------------------------------

@pytest.fixture
def fresh_env(monkeypatch):
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    tracing.use(None)
    yield monkeypatch
    tracing.use(None)


def test_tracing_is_off_unless_configured(fresh_env):
    assert isinstance(tracing.tracer(), tracing.NoopTracer)


def test_keys_without_a_server_address_do_not_fall_back_to_langfuse_cloud(fresh_env, caplog):
    fresh_env.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    fresh_env.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    with caplog.at_level(logging.WARNING):
        assert isinstance(tracing.tracer(), tracing.NoopTracer)
    assert "LANGFUSE_BASE_URL" in caplog.text


def test_keys_without_the_package_leave_tracing_off_and_say_how_to_fix_it(fresh_env, caplog):
    import sys
    fresh_env.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    fresh_env.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    fresh_env.setenv("LANGFUSE_BASE_URL", "http://localhost:3000")
    # None in sys.modules makes `import langfuse` raise ImportError, exactly as
    # on a machine where the `tracing` extra was never installed.
    fresh_env.setitem(sys.modules, "langfuse", None)
    with caplog.at_level(logging.WARNING):
        assert isinstance(tracing.tracer(), tracing.NoopTracer)
    assert "uv sync --extra tracing" in caplog.text


@pytest.mark.parametrize("make_error,expected", [
    # google-genai: one ServerError class for every 5xx, the number in `.code`.
    (lambda: __import__("google.genai.errors", fromlist=["ServerError"]).ServerError(
        503, {"error": {"code": 503, "status": "UNAVAILABLE"}}), 503),
    (lambda: __import__("google.genai.errors", fromlist=["ServerError"]).ServerError(
        504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED"}}), 504),
    # requests, as the image providers raise it.
    (lambda: _http_error(429), 429),
    # Dastango's own Cloudflare adapter: "HTTP 429: ..." in the message.
    (lambda: RuntimeError("HTTP 429: daily free allocation used up"), 429),
    # No answer at all: nothing to record.
    (lambda: ConnectionError("refused"), None),
    (lambda: ValueError("bad JSON"), None),
])
def test_a_failed_call_records_the_http_status_the_provider_answered(spans, make_error, expected):
    error = make_error()
    with pytest.raises(type(error)):
        with tracing.model_call("llm", "story", "gemini"):
            raise error
    (span,) = spans.spans
    assert span.get("http_status") == expected
    assert span["error"] == type(error).__name__          # still the class, never the text


def _http_error(status: int):
    import requests
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


def test_full_configuration_uses_langfuse_at_that_address(fresh_env):
    # Optional package: the rest of this file runs without it, as the app does.
    langfuse = pytest.importorskip("langfuse")
    made = {}

    class FakeLangfuse:
        def __init__(self, **kwargs):
            made.update(kwargs)

    fresh_env.setattr(langfuse, "Langfuse", FakeLangfuse)
    fresh_env.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    fresh_env.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    fresh_env.setenv("LANGFUSE_BASE_URL", "http://localhost:3000")
    assert isinstance(tracing.tracer(), tracing.LangfuseTracer)
    assert made == {"public_key": "pk-lf-test", "secret_key": "sk-lf-test",
                    "base_url": "http://localhost:3000"}


def test_the_langfuse_adapter_sends_one_generation_per_call():
    """What reaches the SDK: metadata only, one trace per job, one session per film."""
    langfuse = pytest.importorskip("langfuse")
    calls = {"start": [], "update": [], "end": 0, "propagate": []}

    class Observation:
        def update(self, **kwargs):
            calls["update"].append(kwargs)

        def end(self):
            calls["end"] += 1

    class Client:
        create_trace_id = staticmethod(langfuse.Langfuse.create_trace_id)

        def start_observation(self, **kwargs):
            calls["start"].append(kwargs)
            return Observation()

    class Propagate:
        def __init__(self, **kwargs):
            calls["propagate"].append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    adapter = tracing.LangfuseTracer(Client(), Propagate)
    job = {"job_id": "job-1", "project_id": "project-1", "job_kind": "render",
           "user_id": "user-abc", "root_id": "abcdef0123456789"}
    started = {"kind": "llm", "role": "story", "provider": "groq", "model": "gpt-oss",
               "job_id": "job-1", "project_id": "project-1", "job_kind": "render"}
    handle = adapter.start(started, {"prompt_chars": 40}, job)
    adapter.end(handle, {**started, "ok": False, "error": "APITimeoutError",
                         "input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
                {"answer_chars": 9})

    (start,) = calls["start"]
    # Named for what it does, not for the provider or model, and nested under the job.
    assert start["name"] == "write-script" and start["as_type"] == "generation"
    assert start["model"] == "gpt-oss"
    assert start["trace_context"] == {"trace_id": langfuse.Langfuse.create_trace_id(seed="job-1"),
                                      "parent_span_id": "abcdef0123456789"}
    assert start["input"] == {"prompt_chars": 40}
    assert calls["propagate"] == [{"session_id": "project-1", "user_id": "user-abc",
                                   "trace_name": "render-film", "tags": ["render"]}]
    (update,) = calls["update"]
    assert update["output"] == {"answer_chars": 9}
    assert update["usage_details"] == {"input": 12, "output": 3, "total": 15}
    assert (update["level"], update["status_message"]) == ("ERROR", "APITimeoutError")
    assert calls["end"] == 1

    # With the provider's HTTP answer known, Langfuse's status says which failure it was.
    second = adapter.start(started, {}, job)
    adapter.end(second, {**started, "ok": False, "error": "ServerError", "http_status": 503}, {})
    assert calls["update"][-1]["status_message"] == "ServerError (HTTP 503)"

    # The job itself: a root `chain` observation named for the job, with its settings.
    adapter.start_job(job, {"scene_count": 3})
    root = calls["start"][-1]
    assert (root["name"], root["as_type"], root["input"]) == ("render-film", "chain",
                                                              {"scene_count": 3})
    assert "parent_span_id" not in root["trace_context"]
