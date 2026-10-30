"""LLM access driven by the model settings layer (`config/providers.yaml`).

Callers ask for a role, not a model:

    client = get_llm_client("story")
    client.generate("...")                      # free text
    client.generate_structured("...", Schema)   # validated Pydantic object

The client walks that role's provider chain: the first provider with credentials
is used, and if a call fails (quota, outage, bad JSON) the next one takes over.
`provider == "mock"` means no real model is configured and the caller should use
its offline fallback (the template script, the keyword classifier, ...).
"""
from __future__ import annotations
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from shared import providers, tracing
from shared.providers import ProviderSpec, is_daily_quota, is_timeout
from shared.utils.logging import get_logger

log = get_logger("llm_client")

# The SDK warns about automatic function calling on every plain generate_content.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

T = TypeVar("T", bound=BaseModel)

MOCK = "mock"

# Reasoning models spend output tokens thinking before they answer, so a small
# cap returns nothing at all. Measured on gemini-flash-latest: 20 tokens -> empty
# (finish_reason MAX_TOKENS), 600 -> "OK".
MIN_OUTPUT_TOKENS = 600

# OpenAI-compatible providers: name -> (base URL, API key env var)
OPENAI_COMPATIBLE: Dict[str, tuple] = {
    "openai": (None, "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": (None, None),   # base URL comes from OLLAMA_HOST; no key needed
}


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMClient:
    """Single entry point for text + structured generation for one role."""

    def __init__(self, role: str = "story"):
        self.role = role
        self.specs: List[ProviderSpec] = [s for s in providers.chain(role)
                                          if s.provider != "mymemory"]
        log.info("LLM role=%s chain=[%s]", role,
                 ", ".join(str(s) for s in self.specs) or MOCK)

    # ---- what's active ---------------------------------------------------

    @property
    def spec(self) -> Optional[ProviderSpec]:
        return self.specs[0] if self.specs else None

    @property
    def provider(self) -> str:
        return self.spec.provider if self.spec else MOCK

    @property
    def model(self) -> str:
        return (self.spec.model or self.spec.provider) if self.spec else "mock-1.0"

    # ---- text generation -------------------------------------------------

    def generate(self, prompt: str, system: str = "", temperature: float = 0.7,
                 max_tokens: int = 2000) -> LLMResponse:
        errors = []
        for position, spec in enumerate(self.specs, start=1):
            if spec.provider == MOCK:
                break
            try:
                with tracing.model_call("llm", self.role, spec.provider, spec.model,
                                        position=position,
                                        input=_sizes(prompt, system, max_tokens)) as call:
                    text = self._call(spec, prompt, system, temperature, max_tokens)
                    if not text.strip():
                        raise ValueError("empty response")
                    call.output(answer_chars=len(text))
                return LLMResponse(text=text, provider=spec.provider,
                                   model=spec.model or spec.provider)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{spec.provider}: {e}")
                log.warning("%s failed for role %s (%s) — trying next provider",
                            spec.provider, self.role, _reason(e, spec))
        if errors:
            log.warning("every provider failed for role %s: %s", self.role, "; ".join(errors))
        # Mock: callers fall back to their own offline path.
        return LLMResponse(text=f"[mock-llm] {prompt[:120]}", provider=MOCK, model="mock-1.0")

    # ---- structured (JSON) generation -----------------------------------

    def generate_structured(self, prompt: str, schema: Type[T], system: str = "",
                            temperature: float = 0.5, max_tokens: int = 3000,
                            max_retries: int = 1) -> T:
        """Returns a validated Pydantic instance, or raises if no provider can."""
        sys_msg = (
            (system + "\n\n" if system else "")
            + "You MUST respond with a single valid JSON object that matches the requested "
              "schema. Do not include markdown fences, comments, or any text outside the JSON."
        )
        errors: List[str] = []
        for position, spec in enumerate(self.specs, start=1):
            if spec.provider == MOCK:
                break
            attempt_prompt = prompt
            for attempt in range(max_retries + 1):
                try:
                    # Validation is inside the span: an answer that is not usable
                    # JSON is a failed call as far as the film is concerned.
                    with tracing.model_call("llm", self.role, spec.provider, spec.model,
                                            position=position, attempt=attempt + 1,
                                            input={**_sizes(attempt_prompt, sys_msg, max_tokens),
                                                   "schema": schema.__name__}) as call:
                        text = self._call(spec, attempt_prompt, sys_msg, temperature,
                                          max_tokens, schema=schema)
                        call.output(answer_chars=len(text))
                        return schema.model_validate(json.loads(_extract_json(text)))
                except (json.JSONDecodeError, ValidationError, TypeError) as e:
                    # The model answered, but not with usable JSON: worth one more try
                    # with the error fed back to it.
                    errors.append(f"{spec.provider}: {type(e).__name__}: {e}")
                    log.warning("%s structured attempt %d failed for role %s: %s",
                                spec.provider, attempt + 1, self.role, str(e)[:200])
                    attempt_prompt = (
                        f"{prompt}\n\nPrevious response was invalid: {e}\n"
                        "Return only valid JSON matching the schema."
                    )
                except Exception as e:  # noqa: BLE001
                    # The provider itself failed (quota, network, outage, timeout): move on.
                    errors.append(f"{spec.provider}: {type(e).__name__}: {e}")
                    log.warning("%s failed for role %s (%s) — trying next provider",
                                spec.provider, self.role, _reason(e, spec))
                    break
        raise RuntimeError(
            f"no provider produced valid structured output for role '{self.role}'"
            + (f": {'; '.join(errors[-3:])}" if errors else
               " (no model configured — use the offline fallback)")
        )

    # ---- provider adapters ----------------------------------------------

    def _call(self, spec: ProviderSpec, prompt: str, system: str, temperature: float,
              max_tokens: int, schema: Optional[Type[BaseModel]] = None) -> str:
        if spec.provider == "gemini":
            return _gemini(spec, prompt, system, temperature, max_tokens, schema)
        if spec.provider == "anthropic":
            return _anthropic(spec, prompt, system, temperature, max_tokens)
        if spec.provider in OPENAI_COMPATIBLE:
            return _openai_compatible(spec, prompt, system, temperature, max_tokens, schema)
        raise ValueError(f"unknown LLM provider '{spec.provider}'")


# ---- adapters --------------------------------------------------------------

def _gemini(spec: ProviderSpec, prompt: str, system: str, temperature: float,
            max_tokens: int, schema: Optional[Type[BaseModel]] = None) -> str:
    from google import genai
    from google.genai import types
    # Unlike every other client here, google-genai takes milliseconds. With no
    # timeout it passes None to httpx, which waits forever: 36 minutes in the
    # 2026-10-28 baseline run before it was stopped. It does not retry unless asked.
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                          http_options=types.HttpOptions(timeout=int(spec.timeout_s * 1000)))
    config: Dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max(max_tokens, MIN_OUTPUT_TOKENS),
    }
    if system:
        config["system_instruction"] = system
    if schema is not None:
        # Native structured output: the model is constrained to the schema.
        config["response_mime_type"] = "application/json"
        config["response_schema"] = schema
    resp = client.models.generate_content(
        model=spec.model or "gemini-flash-latest",
        contents=prompt,
        config=types.GenerateContentConfig(**config),
    )
    usage = getattr(resp, "usage_metadata", None)
    if usage is not None:
        # The total includes the model's thinking, which the output count leaves out.
        tracing.record_tokens(input=usage.prompt_token_count,
                              output=usage.candidates_token_count,
                              total=usage.total_token_count)
    if not resp.text:
        finish = getattr(resp.candidates[0], "finish_reason", None) if resp.candidates else None
        raise RuntimeError(
            f"no text in response (finish_reason={finish})"
            + (" — the model used its whole budget thinking; raise max_tokens"
               if str(finish).endswith("MAX_TOKENS") else "")
        )
    return resp.text


def _anthropic(spec: ProviderSpec, prompt: str, system: str, temperature: float,
               max_tokens: int) -> str:
    import anthropic
    # max_retries=0: the provider chain is the retry (see _openai_compatible).
    client = anthropic.Anthropic(timeout=spec.timeout_s, max_retries=0)
    resp = client.messages.create(
        model=spec.model or "claude-sonnet-4-6",
        max_tokens=max_tokens,
        temperature=temperature,
        system=system or "You are a helpful assistant.",
        messages=[{"role": "user", "content": prompt}],
    )
    usage = getattr(resp, "usage", None)
    if usage is not None:
        tracing.record_tokens(input=usage.input_tokens, output=usage.output_tokens,
                              total=usage.input_tokens + usage.output_tokens)
    return "".join(b.text for b in resp.content if hasattr(b, "text"))


def _openai_compatible(spec: ProviderSpec, prompt: str, system: str, temperature: float,
                       max_tokens: int, schema: Optional[Type[BaseModel]] = None) -> str:
    """OpenAI, Groq, OpenRouter and Ollama all speak the chat-completions API."""
    from openai import OpenAI
    base_url, key_env = OPENAI_COMPATIBLE[spec.provider]
    if spec.provider == "ollama":
        base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/v1"
        api_key = "ollama"   # ignored by Ollama, required by the client
    else:
        api_key = os.environ[key_env] if key_env else None
    # The SDK's defaults are a 600 s read timeout and two retries of its own,
    # so one hung provider held the chain for up to ~30 minutes. max_retries=0
    # because the chain is the retry: a failure goes straight to the next provider.
    limits: Dict[str, Any] = {"timeout": spec.timeout_s, "max_retries": 0}
    client = (OpenAI(api_key=api_key, base_url=base_url, **limits) if base_url
              else OpenAI(api_key=api_key, **limits))

    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    model = spec.model or "gpt-4o-mini"
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max(max_tokens, MIN_OUTPUT_TOKENS),
    }
    if schema is not None:
        kwargs["response_format"] = {"type": "json_object"}
    if "gpt-oss" in model:
        # Open-weight reasoning models: keep the thinking short so the answer fits.
        kwargs["extra_body"] = {"reasoning_effort": "low"}
    resp = client.chat.completions.create(**kwargs)
    usage = getattr(resp, "usage", None)
    if usage is not None:
        tracing.record_tokens(input=usage.prompt_tokens, output=usage.completion_tokens,
                              total=usage.total_tokens)
    text = resp.choices[0].message.content or ""
    if not text.strip():
        finish = resp.choices[0].finish_reason
        raise RuntimeError(f"no text in response (finish_reason={finish})")
    return text


# ---- helpers ---------------------------------------------------------------

def _sizes(prompt: str, system: str, max_tokens: int) -> Dict[str, Any]:
    """A call's input for its trace: how big it was, never what it said."""
    return {"prompt_chars": len(prompt), "system_chars": len(system), "max_tokens": max_tokens}


def _reason(error: Exception, spec: ProviderSpec) -> str:
    """Why a provider failed, for the log: a timeout says how long it waited."""
    if is_timeout(error):
        return f"no answer within {spec.timeout_s:g} s"
    if is_daily_quota(error):
        return f"daily quota used up: {str(error)[:160]}"
    return str(error)[:200]


_FENCE =re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a response that may carry fences or prose."""
    text = _FENCE.sub("", text.strip()).strip()
    if not text.startswith(("{", "[")):
        match = re.search(r"\{.*\}|\[.*\]", text, flags=re.DOTALL)
        if match:
            text = match.group(0)
    return text


_CLIENTS: Dict[str, LLMClient] = {}


def get_llm_client(role: str = "story", force_new: bool = False) -> LLMClient:
    """Cached client per role (the chain is resolved once per process)."""
    if force_new or role not in _CLIENTS:
        _CLIENTS[role] = LLMClient(role=role)
    return _CLIENTS[role]


def reset_clients() -> None:
    """Drop cached clients — used by tests that change the environment."""
    _CLIENTS.clear()
