"""Subtitle translation tool.

Strategy (first success wins):
1. The LLM chain configured for the `translate` role: one request per language,
   answering with a JSON array aligned 1:1 with the input lines.
2. MyMemory (free, no key), when it's in that chain: lines are batched into
   <=450-char newline-joined requests. Anonymous use allows ~5,000 chars/day;
   set MYMEMORY_EMAIL in .env to raise that to ~50,000.

If every provider fails the tool FAILS — callers must skip the language rather
than ship an untranslated track under a foreign label.
"""
from __future__ import annotations
import json
import os
import re
import time
from typing import List

import requests

from mcp.base_tool import BaseTool, ToolResult
from shared import providers, tracing
from shared.languages import canonical, mymemory_code
from shared.providers import is_timeout
from shared.utils.logging import get_logger

from .llm_client import get_llm_client

log = get_logger("translate")

_MYMEMORY_MAX_CHARS = 450  # MyMemory rejects requests over 500 chars
# Called directly rather than through deep_translator, whose MyMemoryTranslator
# (1.11.4) sends requests.get() with no timeout and takes none (DST-18).
_MYMEMORY_URL = "https://api.mymemory.translated.net/get"


class TranslateTool(BaseTool):
    name = "text.translate"
    description = "Translate a list of subtitle lines into a target language (LLM, then MyMemory)."
    category = "llm"

    def run(self, lines: List[str], target_language: str, **_) -> ToolResult:
        lang = canonical(target_language)
        if not lang:
            return ToolResult(success=False, error=f"unsupported language '{target_language}'")
        if lang == "English" or not lines:
            return ToolResult(success=True, data=list(lines), metadata={"provider": "identity"})

        errors: List[str] = []
        enabled = {spec.provider for spec in providers.chain("translate")}
        client = get_llm_client("translate")
        if client.provider != "mock":
            try:
                out, served_by = self._llm(client, lines, lang)
                # Who answered, not who was asked first: the chain falls through.
                return ToolResult(success=True, data=out,
                                  metadata={"provider": f"llm:{served_by}"})
            except Exception as e:  # noqa: BLE001
                errors.append(f"llm: {e}")
                log.info("LLM translation to %s failed (%s) — trying MyMemory", lang, e)
        if "mymemory" in enabled:
            try:
                out = self._mymemory(lines, lang)
                return ToolResult(success=True, data=out, metadata={"provider": "mymemory"})
            except Exception as e:  # noqa: BLE001
                errors.append(f"mymemory: {e}")
        return ToolResult(success=False,
                          error="; ".join(errors) or "no translation provider configured")

    # ---- providers -------------------------------------------------------

    @staticmethod
    def _llm(client, lines: List[str], lang: str) -> tuple[List[str], str]:
        prompt = (
            f"Translate each subtitle line below into {lang}. Keep the tone and keep "
            f"lines short enough to read on screen.\n"
            f"Return ONLY a JSON array of {len(lines)} strings, in the same order, "
            f"one translation per input line.\n\n"
            + json.dumps(lines, ensure_ascii=False)
        )
        response = client.generate(prompt, system="You are a professional subtitle translator.",
                                   temperature=0.2, max_tokens=4000)
        text = response.text
        match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not match:
            raise ValueError("no JSON array in response")
        out = json.loads(match.group(0))
        if not isinstance(out, list) or len(out) != len(lines) \
                or not all(isinstance(s, str) and s.strip() for s in out):
            raise ValueError(f"expected {len(lines)} non-empty strings, got {len(out) if isinstance(out, list) else type(out).__name__}")
        return [s.strip() for s in out], response.provider

    def _mymemory(self, lines: List[str], lang: str) -> List[str]:
        code = mymemory_code(lang)
        if not code:
            raise ValueError(f"MyMemory has no code for {lang}")
        timeout = providers.timeout_s("translate", "mymemory")
        chain = [s.provider for s in providers.chain("translate")]
        position = chain.index("mymemory") + 1 if "mymemory" in chain else 1

        out: List[str] = []
        for chunk in _chunks(lines, _MYMEMORY_MAX_CHARS):
            joined = "\n".join(s.replace("\n", " ") for s in chunk)
            result = self._call_with_retry(joined, code, timeout, position)
            parts = [p.strip() for p in result.split("\n")]
            if len(parts) != len(chunk):
                # Line breaks weren't preserved — fall back to one request per line.
                parts = [self._call_with_retry(s, code, timeout, position).strip()
                         for s in chunk]
            out.extend(parts)
        return out

    @staticmethod
    def _call_with_retry(text: str, target: str, timeout: float, position: int = 1,
                         attempts: int = 3) -> str:
        last: Exception | None = None
        for i in range(attempts):
            try:
                with tracing.model_call("translate", "translate", "mymemory", position=position,
                                        attempt=i + 1, input={"text_chars": len(text)}) as call:
                    result = _mymemory_request(text, target, timeout)
                    if not result or "MYMEMORY WARNING" in result.upper():
                        raise RuntimeError(result or "empty translation")
                    call.output(answer_chars=len(result))
                return result
            except Exception as e:  # noqa: BLE001
                last = e
                if is_timeout(e):
                    # Like every other provider: a timeout is not retried, so the
                    # worst case is one limit, not three plus back-off.
                    raise RuntimeError(f"no answer within {timeout:g} s") from e
                if "MYMEMORY WARNING" in str(e).upper():
                    break  # daily quota used up — retrying won't help
                time.sleep(1.5 * (i + 1))
        raise RuntimeError(str(last))


def _mymemory_request(text: str, target: str, timeout: float) -> str:
    """One MyMemory lookup: the same request deep_translator made, with a time limit."""
    text = text.strip()
    # The checks deep_translator made before sending: MyMemory answers an
    # over-long query with its error message as the "translation".
    if not text:
        return text
    if len(text) > 500:
        raise ValueError(f"{len(text)} characters is over MyMemory's 500-character limit")
    params = {"q": text, "langpair": f"en-GB|{target}"}
    if os.getenv("MYMEMORY_EMAIL"):
        params["de"] = os.environ["MYMEMORY_EMAIL"]
    response = requests.get(_MYMEMORY_URL, params=params, timeout=timeout)
    if response.status_code != 200:
        # "HTTP 429: ..." is the form tracing.http_status reads the status from.
        raise RuntimeError(f"HTTP {response.status_code}: MyMemory refused the request")
    data = response.json() or {}
    translation = (data.get("responseData") or {}).get("translatedText")
    if translation:
        return translation
    # deep_translator fell back to the best match when there was no main answer.
    matches = data.get("matches") or []
    return matches[0].get("translation", "") if matches else ""


def _chunks(lines: List[str], max_chars: int) -> List[List[str]]:
    chunks: List[List[str]] = []
    current: List[str] = []
    size = 0
    for line in lines:
        add = len(line) + (1 if current else 0)
        if current and size + add > max_chars:
            chunks.append(current)
            current, size = [], 0
            add = len(line)
        current.append(line)
        size += add
    if current:
        chunks.append(current)
    return chunks
