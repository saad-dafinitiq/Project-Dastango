"""DST-20: a 429 that means today's quota is used up falls through at once.

A daily allowance will not come back in the seconds a retry waits, so it skips
the retries, is marked on its trace and is named on the job's completion event.
A short-term rate limit keeps the retries it always had.

Messages marked "measured" are copied from the DST-10 baseline logs
(2026-10-28/29); the rest follow each provider's documented error format.
"""
from __future__ import annotations

import httpx
import openai
import pytest
import requests
from google.genai.errors import ClientError

from mcp.tools.llm_tools import llm_client, translate_tool
from mcp.tools.llm_tools.translate_tool import TranslateTool
from mcp.tools.vision_tools import image_gen_tool
from mcp.tools.vision_tools.image_gen_tool import ImageGenTool, _is_permanent
from shared import providers, tracing
from shared.providers import is_daily_quota
from tests.unit.test_model_call_tracing import chain, spans  # noqa: F401
from tests.unit.test_mymemory_timeout import FakeResponse, _orchestrator

# Measured up to "usage. "; the body is cut there in the log. The error code
# after it is Cloudflare's for this refusal (4006), which contains "400".
CLOUDFLARE_DAILY = ('HTTP 429: {"errors":[{"message":"AiError: AiError: you have used up your '
                    "daily free allocation of 10,000 neurons, please upgrade to Cloudflare's "
                    'Workers Paid plan if you would like to continue usage. ","code":4006}],'
                    '"success":false}')
CLOUDFLARE_BUSY = ('HTTP 429: {"errors":[{"message":"Capacity temporarily exceeded, please '
                   'try again.","code":3040}],"success":false}')
# Measured (organisation id replaced).
GROQ_PER_MINUTE = ("Rate limit reached for model `openai/gpt-oss-120b` in organization "
                   "`org_test` service tier `on_demand` on tokens per minute (TPM): Limit 8000, "
                   "Used 5843, Requested 2729. Please try again in 4.29s. Need more tokens? "
                   "Upgrade to Dev Tier today at https://console.groq.com/settings/billing")
GROQ_PER_DAY = ("Rate limit reached for model `openai/gpt-oss-120b` in organization `org_test` "
                "service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199400, "
                "Requested 2729. Please try again in 14m24s.")
MYMEMORY_DAILY = ("MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY. "
                  "NEXT AVAILABLE IN 10 HOURS 12 MINUTES 31 SECONDS")


def _groq_error(message: str):
    """What the OpenAI SDK raises for Groq and OpenRouter: the status on `.status_code`."""
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.test/v1"))
    return openai.RateLimitError(f"Error code: 429 - {{'error': {{'message': '{message}'}}}}",
                                 response=response, body=None)


def _gemini_error(quota_id: str):
    # The message is measured; it is the same for a per-minute and a per-day
    # limit. Only the quota id in the details tells them apart.
    return ClientError(429, {"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": "You exceeded your current quota, please check your plan and billing "
                   "details.",
        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                     "violations": [{"quotaMetric": "generativelanguage.googleapis.com/"
                                                    "generate_content_free_tier_requests",
                                     "quotaId": quota_id}]}]}})


def _requests_error(status: int):
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} Client Error", response=response)


# ---- the rule -----------------------------------------------------------------

@pytest.mark.parametrize("make_error", [
    lambda: RuntimeError(CLOUDFLARE_DAILY),
    lambda: _gemini_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
    lambda: _groq_error(GROQ_PER_DAY),
    lambda: _groq_error("Rate limit exceeded: free-models-per-day. Add 10 credits to unlock "
                        "1000 free model requests per day"),      # OpenRouter
    lambda: RuntimeError(f"HTTP 429: MyMemory refused the request: {MYMEMORY_DAILY}"),
], ids=["cloudflare", "gemini", "groq", "openrouter", "mymemory"])
def test_a_daily_quota_429_is_recognised(make_error):
    assert is_daily_quota(make_error())


@pytest.mark.parametrize("make_error", [
    lambda: RuntimeError(CLOUDFLARE_BUSY),
    lambda: _gemini_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"),
    lambda: _groq_error(GROQ_PER_MINUTE),           # ends "Upgrade to Dev Tier today"
    lambda: _requests_error(429),                   # a bare 429, as keyless Pollinations sends
    lambda: RuntimeError("HTTP 503: daily maintenance"),   # the right words, not a 429
    lambda: ValueError("you have used up your daily free allocation"),  # no status at all
], ids=["cloudflare-busy", "gemini-per-minute", "groq-per-minute", "bare-429", "503",
        "no-status"])
def test_anything_else_is_not_a_daily_quota(make_error):
    assert not is_daily_quota(make_error())


def test_the_cause_chain_is_read():
    """Adapters re-raise; the provider's words may sit on the cause."""
    try:
        try:
            raise RuntimeError(CLOUDFLARE_DAILY)
        except RuntimeError as inner:
            raise RuntimeError("image failed") from inner
    except RuntimeError as outer:
        assert is_daily_quota(outer)


@pytest.mark.parametrize("make_error,permanent", [
    (lambda: RuntimeError(CLOUDFLARE_DAILY), False),      # "4006" no longer reads as a 400
    (lambda: RuntimeError("HTTP 429: Limit 14400, Used 14390, try again in 2s"), False),
    (lambda: RuntimeError('HTTP 400: {"errors":[{"code":8007}]}'), True),   # NSFW refusal
    (lambda: _requests_error(401), True),
    (lambda: _requests_error(403), True),
    (lambda: RuntimeError("Invalid API key provided"), True),
])
def test_a_permanent_failure_is_decided_by_its_status_not_a_substring(make_error, permanent):
    assert _is_permanent(make_error()) is permanent


# ---- images: the retry loop -----------------------------------------------------

@pytest.fixture
def image_chain(chain, monkeypatch):  # noqa: F811
    """Cloudflare with its three attempts, then the placeholder; no real waiting."""
    sleeps = []
    monkeypatch.setattr(image_gen_tool.time, "sleep", sleeps.append)
    monkeypatch.delenv("PROVIDER_IMAGE", raising=False)     # conftest pins the placeholder
    chain("""
        roles:
          image:
            - {provider: cloudflare, retries: 3}
            - {provider: placeholder}
    """)
    return sleeps


def _cloudflare_answers(monkeypatch, *answers):
    """Each call takes the next answer: an error message to raise, or None to succeed."""
    calls = []

    def fake(self, spec, prompt, negative, out, w, h, seed):
        calls.append(1)
        answer = answers[min(len(calls), len(answers)) - 1]
        if answer is not None:
            raise RuntimeError(answer)
        return self._provider_placeholder(spec, prompt, negative, out, w, h, seed)

    monkeypatch.setattr(ImageGenTool, "_provider_cloudflare", fake)
    return calls


def _image(tmp_path):
    return ImageGenTool().run(prompt="a lighthouse", out_path=str(tmp_path / "a.png"),
                              width=64, height=36)


def test_a_daily_quota_falls_through_with_no_retries(tmp_path, image_chain, monkeypatch, spans):  # noqa: F811
    calls = _cloudflare_answers(monkeypatch, CLOUDFLARE_DAILY)
    with providers.noting_daily_quotas() as used_up:
        result = _image(tmp_path)

    assert result.success and result.metadata["provider"] == "placeholder"
    assert len(calls) == 1 and image_chain == []           # asked once, never waited
    cloudflare = spans.spans[0]
    assert (cloudflare["provider"], cloudflare["ok"], cloudflare["http_status"],
            cloudflare["daily_quota"]) == ("cloudflare", False, 429, True)
    assert used_up == {"image:cloudflare"}


def test_a_short_term_429_keeps_its_three_attempts(tmp_path, image_chain, monkeypatch, spans):  # noqa: F811
    calls = _cloudflare_answers(monkeypatch, CLOUDFLARE_BUSY)
    with providers.noting_daily_quotas() as used_up:
        result = _image(tmp_path)

    assert result.metadata["provider"] == "placeholder"
    assert len(calls) == 3 and image_chain == [1.5, 3.0]   # the back-off it always had
    assert [s["daily_quota"] for s in spans.spans[:3]] == [False, False, False]
    assert used_up == set()


def test_a_short_term_429_can_still_be_rescued_by_a_retry(tmp_path, image_chain, monkeypatch):
    calls = _cloudflare_answers(monkeypatch, CLOUDFLARE_BUSY, None)
    result = _image(tmp_path)
    assert (result.metadata["provider"], result.metadata["attempt"]) == ("cloudflare", 2)
    assert len(calls) == 2


def test_a_rate_limit_quoting_a_number_with_400_in_it_is_retried(tmp_path, image_chain,
                                                                 monkeypatch):
    """Before DST-20 the substring "400" in "14400" ended the retries."""
    calls = _cloudflare_answers(monkeypatch, "HTTP 429: Limit 14400, Used 14390, try again")
    _image(tmp_path)
    assert len(calls) == 3


# ---- language models and MyMemory ------------------------------------------------

def test_a_script_model_out_of_daily_quota_is_noted_and_skipped(chain, monkeypatch, spans):  # noqa: F811
    calls = []

    def gemini_out_of_quota(*args, **kwargs):
        calls.append(1)
        raise _gemini_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    monkeypatch.setattr(llm_client, "_gemini", gemini_out_of_quota)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    chain("""
        roles:
          story:
            - {provider: gemini, requires: [GEMINI_API_KEY]}
            - {provider: mock}
    """)
    with providers.noting_daily_quotas() as used_up:
        assert llm_client.LLMClient("story").generate("hi").provider == "mock"

    assert len(calls) == 1
    assert spans.spans[0]["daily_quota"] is True
    assert used_up == {"story:gemini"}


@pytest.mark.parametrize("body,attempts", [
    (MYMEMORY_DAILY, 1),
    ("Too many requests, slow down", 3),
])
def test_mymemory_stops_at_a_daily_quota_429_and_retries_a_short_one(chain, monkeypatch,  # noqa: F811
                                                                     body, attempts):
    calls = []

    def answering_429(url, params=None, timeout=None, **_):
        calls.append(1)
        return FakeResponse(429, {}, text=body)

    monkeypatch.setattr(translate_tool.requests, "get", answering_429)
    monkeypatch.setattr(translate_tool.time, "sleep", lambda _s: None)
    chain("roles:\n  translate:\n    - {provider: mymemory}\n")
    result = TranslateTool().run(lines=["Hello."], target_language="French")

    assert result.success is False and "HTTP 429" in result.error
    assert len(calls) == attempts


# ---- the job's completion event ------------------------------------------------

def test_the_completion_event_names_the_provider_whose_quota_ran_out(isolated_dirs,
                                                                     image_chain, monkeypatch):
    """A real run: images are drawn in run_jobs' worker threads, so this also
    checks the job's record follows them there."""
    calls = _cloudflare_answers(monkeypatch, CLOUDFLARE_DAILY)
    events = []
    _orchestrator(isolated_dirs).run_full(
        "A kite flies over a quiet town", on_event=events.append, target_duration_s=20,
        scene_count=2, with_bgm=False, with_subtitles=False, width=320, height=180, fps=12)

    done = events[-1]
    assert (done.phase, done.status) == ("complete", "complete")   # still a finished film
    assert done.payload["daily_quota_used_up"] == ["image:cloudflare"]
    assert "daily quota used up, the next provider took over: image:cloudflare" in done.message
    assert len(calls) > 1 and image_chain == []          # one call per image, no waiting


def test_the_storyboard_event_names_it_too(isolated_dirs, image_chain, monkeypatch):
    _cloudflare_answers(monkeypatch, CLOUDFLARE_DAILY)
    events = []
    _orchestrator(isolated_dirs).plan("A kite flies over a quiet town", on_event=events.append,
                                      target_duration_s=20, scene_count=2,
                                      preview_width=320, preview_height=180)
    done = events[-1]
    assert (done.phase, done.status) == ("storyboard", "complete")
    assert done.payload["daily_quota_used_up"] == ["image:cloudflare"]


def test_nothing_is_reported_when_no_quota_ran_out(isolated_dirs):
    events = []
    _orchestrator(isolated_dirs).run_full(
        "A kite flies over a quiet town", on_event=events.append, target_duration_s=20,
        scene_count=2, with_bgm=False, with_subtitles=False, width=320, height=180, fps=12)
    assert "daily_quota_used_up" not in events[-1].payload
    assert "daily quota" not in events[-1].message
    # And nothing leaks out of a finished job into the next one.
    assert providers.daily_quotas_used_up() == []


def test_one_job_does_not_inherit_anothers_record(isolated_dirs, image_chain, monkeypatch):
    _cloudflare_answers(monkeypatch, CLOUDFLARE_DAILY)
    orch = _orchestrator(isolated_dirs)
    orch.plan("A kite", target_duration_s=20, scene_count=2,
              preview_width=320, preview_height=180)
    monkeypatch.setattr(ImageGenTool, "_provider_cloudflare", ImageGenTool._provider_placeholder)
    events = []
    orch.plan("A boat", on_event=events.append, target_duration_s=20, scene_count=2,
              preview_width=320, preview_height=180)
    assert "daily_quota_used_up" not in events[-1].payload


def test_tracing_says_why_a_call_failed(spans):  # noqa: F811
    """The status line in Langfuse reads "RuntimeError (HTTP 429, daily quota used up)"."""
    observation = type("Obs", (), {"update": lambda self, **k: updates.update(k),
                                   "end": lambda self: None})()
    updates: dict = {}
    tracing.LangfuseTracer(client=None, propagate_attributes=None).end(
        observation, {"ok": False, "error": "RuntimeError", "http_status": 429,
                      "daily_quota": True}, {})
    assert updates["status_message"] == "RuntimeError (HTTP 429, daily quota used up)"
