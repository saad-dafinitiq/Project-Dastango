"""Tracing for every model call: one generation per provider attempt, nested per job.

The four places that call a model (the LLM client, the image tool, the TTS tool
and MyMemory in the translate tool) wrap each attempt in `model_call(...)`; the
worker wraps each job in `job_trace(...)`. Nothing else in the pipeline knows
tracing exists.

Off unless all of LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL
are set and the optional package is installed (the `tracing` extra in pyproject.toml).
LANGFUSE_BASE_URL is required on purpose: without it the Langfuse SDK silently
defaults to Langfuse Cloud, and where traces go has to be a deliberate choice
(Cloud or a self-hosted server), never a default.

In Langfuse each job is one trace with a root observation (`plan-storyboard`,
`render-film`, ...) and every model call nested under it as a generation named
for what it does (`write-script`, `generate-image`, ...). Each film is one
session, the job kind is a tag, and the owner's internal account id is the
user id. Names are stable on purpose: dashboards and filters refer to them, so
providers, models and ids live in attributes, never in names (Langfuse's
observability best practices).

What is sent is a fixed set of fields. Metadata: SPAN_FIELDS. Input and output:
settings and sizes (JOB_INPUT_FIELDS, JOB_OUTPUT_FIELDS, IO_FIELDS), never text.
A prompt or an edit request becomes its character count. No scripts, answers,
images, audio or keys.
"""
from __future__ import annotations
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional, Tuple

# http_status lives with the other error readers; it is re-exported from here.
from shared.providers import http_status, is_daily_quota, is_timeout, note_daily_quota
from shared.utils.logging import get_logger

log = get_logger("tracing")

SPAN_FIELDS = (
    "kind", "role", "provider", "model",
    "position",     # place in the chain: 1 is the provider tried first
    "fallback",     # true when an earlier provider in the chain failed
    "attempt",      # try number with this provider (image retries, bad JSON)
    "ok", "error", "timed_out", "latency_ms",
    "http_status",  # the provider's HTTP answer on failure: 503 (overloaded), 504, 429...
    "daily_quota",  # true when that 429 meant today's allowance is used up (not retried)
    "input_tokens", "output_tokens", "total_tokens",
    "job_id", "job_kind", "project_id",
)
# Input and output of a generation: sizes and settings, never content.
IO_FIELDS = ("prompt_chars", "system_chars", "max_tokens", "schema", "answer_chars",
             "width", "height", "native_width", "native_height", "text_chars", "voice")
# A job's settings as the API queued them; `prompt` and `query` arrive as counts.
JOB_INPUT_FIELDS = ("target_duration_s", "scene_count", "subtitle_language", "tts_engine",
                    "with_bgm", "with_subtitles", "burn_subtitles", "with_preview", "phase",
                    "version", "prompt_chars", "request_chars")
JOB_OUTPUT_FIELDS = ("stage", "version", "scenes", "film_s")

# Verb-first names for what a call does, by provider role.
OPERATIONS = {"story": "write-script", "edit_intent": "classify-edit",
              "translate": "translate-subtitles", "image": "generate-image",
              "tts": "synthesize-voice"}
# Trace (and root observation) names by job kind.
TRACE_NAMES = {"plan": "plan-storyboard", "render": "render-film", "edit": "edit-film",
               "revert": "revert-version", "run_full": "make-film",
               "rerun_phase": "rerun-phase"}

# The job being run, if any. ContextVars follow the code into run_jobs' worker
# threads because run_jobs copies the context into each one.
_job: ContextVar[Optional[Dict[str, Any]]] = ContextVar("dastango_job", default=None)
_current: ContextVar[Optional["Call"]] = ContextVar("dastango_model_call", default=None)


def operation_name(role: str) -> str:
    return OPERATIONS.get(role, f"call-{role}")


def trace_name(kind: str) -> str:
    return TRACE_NAMES.get(kind, f"{kind}-job")


class Call:
    """One model call in progress. The adapter adds tokens and an output summary."""

    def __init__(self, fields: Dict[str, Any]):
        self.fields = fields
        self.output_fields: Dict[str, Any] = {}

    def output(self, **summary: Any) -> None:
        self.output_fields.update(summary)


class JobTrace:
    """A job being traced; the worker reports its outcome with finish()."""

    def __init__(self):
        self.output_fields: Dict[str, Any] = {}

    def finish(self, summary: Dict[str, Any]) -> None:
        self.output_fields.update(summary)


class NoopTracer:
    """What runs when tracing is not configured: costs a few dicts and a clock read."""

    def start_job(self, job: Dict[str, Any], input: Dict[str, Any]) -> Any:
        return None

    def end_job(self, handle: Any, job: Dict[str, Any], output: Dict[str, Any]) -> None:
        pass

    def start(self, fields: Dict[str, Any], input: Dict[str, Any], job: Dict[str, Any]) -> Any:
        return None

    def end(self, handle: Any, fields: Dict[str, Any], output: Dict[str, Any]) -> None:
        pass

    def flush(self) -> None:
        pass


class LangfuseTracer:
    """Sends each job as a trace and each model call as a generation inside it."""

    def __init__(self, client: Any, propagate_attributes: Any):
        self._client = client
        self._propagate = propagate_attributes

    def _trace_attributes(self, job: Dict[str, Any]) -> Dict[str, Any]:
        attributes = {"session_id": job.get("project_id"), "user_id": job.get("user_id"),
                      "trace_name": trace_name(job["job_kind"]) if job.get("job_kind") else None,
                      "tags": [job["job_kind"]] if job.get("job_kind") else None}
        return {k: v for k, v in attributes.items() if v}

    def _trace_context(self, seed: str, parent: Optional[str] = None) -> Dict[str, str]:
        # The same seed gives the same trace id, so calls made in different
        # threads (images and voices run in parallel) land in one trace.
        context = {"trace_id": self._client.create_trace_id(seed=seed)}
        if parent:
            context["parent_span_id"] = parent
        return context

    def start_job(self, job: Dict[str, Any], input: Dict[str, Any]) -> Any:
        with self._propagate(**self._trace_attributes(job)):
            return self._client.start_observation(
                trace_context=self._trace_context(job["job_id"]),
                name=trace_name(job["job_kind"]), as_type="chain", input=input or None,
                metadata={k: job[k] for k in ("job_id", "job_kind", "project_id") if job.get(k)})

    def end_job(self, root: Any, job: Dict[str, Any], output: Dict[str, Any]) -> None:
        if root is None:
            return
        ok = job.get("ok", True)
        root.update(output=output or None, level="DEFAULT" if ok else "ERROR",
                    status_message=None if ok else job.get("error"))
        root.end()

    def start(self, fields: Dict[str, Any], input: Dict[str, Any], job: Dict[str, Any]) -> Any:
        kwargs: Dict[str, Any] = {
            "name": operation_name(fields["role"]), "as_type": "generation",
            "model": fields.get("model"), "metadata": fields, "input": input or None,
        }
        seed = job.get("job_id") or fields.get("project_id")
        if seed:
            kwargs["trace_context"] = self._trace_context(seed, job.get("root_id"))
        attributes = self._trace_attributes(job)
        if not attributes:
            return self._client.start_observation(**kwargs)
        with self._propagate(**attributes):
            return self._client.start_observation(**kwargs)

    def end(self, observation: Any, fields: Dict[str, Any], output: Dict[str, Any]) -> None:
        if observation is None:
            return
        usage = {name: fields[f"{name}_tokens"] for name in ("input", "output", "total")
                 if fields.get(f"{name}_tokens") is not None}
        status = None
        if not fields.get("ok"):
            status = fields.get("error")
            if fields.get("http_status"):
                status = f"{status} (HTTP {fields['http_status']}" + (
                    ", daily quota used up)" if fields.get("daily_quota") else ")")
        observation.update(metadata=fields, output=output or None, usage_details=usage or None,
                           level="DEFAULT" if fields.get("ok") else "ERROR",
                           status_message=status)
        observation.end()

    def flush(self) -> None:
        self._client.flush()


_tracer: Optional[Any] = None
_lock = threading.Lock()


def tracer() -> Any:
    global _tracer
    if _tracer is None:
        with _lock:
            if _tracer is None:
                _tracer = _from_env()
    return _tracer


def use(new_tracer: Optional[Any]) -> Optional[Any]:
    """Swap the tracer (tests pass a fake; None means read the environment again)."""
    global _tracer
    with _lock:
        previous, _tracer = _tracer, new_tracer
    return previous


def _from_env() -> Any:
    public, secret = os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")
    base_url = os.getenv("LANGFUSE_BASE_URL")
    if not (public and secret):
        return NoopTracer()
    if not base_url:
        log.warning("Langfuse keys are set but LANGFUSE_BASE_URL is not, so tracing is off "
                    "(without it the SDK would send to Langfuse Cloud)")
        return NoopTracer()
    try:
        from langfuse import Langfuse, propagate_attributes
    except ImportError:
        log.warning("Langfuse keys are set but the package is missing, so tracing is off; "
                    "run `uv sync --extra tracing` (name every extra you use: "
                    "sync removes the others)")
        return NoopTracer()
    log.info("tracing model calls to %s", base_url)
    return LangfuseTracer(Langfuse(public_key=public, secret_key=secret, base_url=base_url),
                          propagate_attributes)


def job_settings(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A job's input for its trace: its settings, with any text reduced to a length."""
    payload = payload or {}
    settings = dict(payload)
    for text_field, count in (("prompt", "prompt_chars"), ("query", "request_chars")):
        if isinstance(payload.get(text_field), str):
            settings[count] = len(payload[text_field])
    return _clean(settings, JOB_INPUT_FIELDS)


def summarize(state: Any) -> Dict[str, Any]:
    """A job's output for its trace: what it produced, from the state it returned."""
    script = getattr(state, "script", None)
    manifest = getattr(getattr(state, "audio", None), "manifest", None)
    return _clean({
        "stage": getattr(state, "stage", None),
        "version": getattr(state, "version", None),
        "scenes": len(script.scenes) if getattr(script, "scenes", None) is not None else None,
        "film_s": round(manifest.total_duration_ms / 1000, 1) if manifest else None,
    }, JOB_OUTPUT_FIELDS)


@contextmanager
def job_trace(job_id: str, project_id: str, kind: str, user_id: Optional[str] = None,
              settings: Optional[Dict[str, Any]] = None) -> Iterator[JobTrace]:
    """Group every model call made while this job runs under one root observation."""
    job: Dict[str, Any] = {"job_id": job_id, "project_id": project_id, "job_kind": kind,
                           "user_id": user_id}
    trace = JobTrace()
    root = _safely(tracer().start_job, job, job_settings(settings))
    job["root_id"] = getattr(root, "id", None)
    token = _job.set(job)
    try:
        yield trace
    except BaseException as e:
        job.update(ok=False, error=type(e).__name__)
        raise
    finally:
        _job.reset(token)
        _safely(tracer().end_job, root, job, _clean(trace.output_fields, JOB_OUTPUT_FIELDS))
        # The SDK sends in batches; flushing here puts a finished job's trace
        # on the server now rather than whenever the next batch fills.
        _safely(tracer().flush)


@contextmanager
def model_call(kind: str, role: str, provider: str, model: Optional[str] = None,
               position: int = 1, attempt: int = 1,
               input: Optional[Dict[str, Any]] = None) -> Iterator[Call]:
    """One attempt at one provider. Re-raises whatever the call raises."""
    job = _job.get() or {}
    fields: Dict[str, Any] = {"kind": kind, "role": role, "provider": provider,
                              "model": model or provider, "position": position,
                              "fallback": position > 1, "attempt": attempt,
                              **{k: job[k] for k in ("job_id", "job_kind", "project_id")
                                 if job.get(k)}}
    call = Call(fields)
    handle = _safely(tracer().start, _clean(fields), _clean(input or {}, IO_FIELDS), job)
    token = _current.set(call)
    started = time.monotonic()
    try:
        yield call
        fields.update(ok=True, timed_out=False)
    except BaseException as e:
        daily_quota = is_daily_quota(e)
        fields.update(ok=False, error=type(e).__name__, timed_out=is_timeout(e),
                      http_status=http_status(e), daily_quota=daily_quota)
        if daily_quota:
            # Every model call passes through here, so this is the one place
            # that sees them all for the job's completion event (DST-20).
            note_daily_quota(role, provider)
        raise
    finally:
        _current.reset(token)
        fields["latency_ms"] = round((time.monotonic() - started) * 1000)
        _safely(tracer().end, handle, _clean(fields),
                _clean(call.output_fields, IO_FIELDS))


def record_tokens(input: Optional[int] = None, output: Optional[int] = None,
                  total: Optional[int] = None) -> None:
    """Called by an adapter with whatever usage its provider reported."""
    call = _current.get()
    if call is None:
        return
    for name, value in (("input", input), ("output", output), ("total", total)):
        if isinstance(value, int) and not isinstance(value, bool):
            call.fields[f"{name}_tokens"] = value


def _clean(fields: Dict[str, Any], allowed: Tuple[str, ...] = SPAN_FIELDS) -> Dict[str, Any]:
    """Only the allowed fields, only plain values, and never a credential's value.

    The allow-lists already keep prompts and keys out; blanking credential
    values from strings as well is a second line in case a provider or model
    name ever comes from somewhere unexpected.
    """
    secrets = [value for name, value in os.environ.items() if len(value) >= 8
               and any(word in name for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))]
    clean: Dict[str, Any] = {}
    for name in allowed:
        value = fields.get(name)
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "***")
            clean[name] = value[:200]
        elif isinstance(value, (bool, int, float)):
            clean[name] = value
    return clean


_warned: set = set()


def _safely(fn, *args) -> Any:
    """Tracing must never break a film: a failure is logged once per kind and dropped."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        kind = type(e).__name__
        if kind not in _warned:
            _warned.add(kind)
            # The class name only: an SDK error can quote a URL or a header.
            log.warning("tracing failed (%s); the run carries on without it", kind)
        return None
