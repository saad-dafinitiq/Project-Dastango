"""Model settings: which provider serves each role, and in what order.

Agents never name a model. They ask for a role (story, image, tts, ...) and get
an ordered chain of providers from `config/providers.yaml`; the first one whose
credentials are present is used, and the next is tried if it fails. Swapping a
model — or adding a paid one later — is a config change, not a code change.

    for spec in chain("image"):
        ...try spec.provider with spec.model / spec.params...

Environment overrides:
    PROVIDERS_FILE=/path/to/providers.yaml   use a different config
    PROVIDER_IMAGE=pollinations              force one provider for a role
    LLM_PROVIDER=gemini|groq|...|mock        legacy: forces all LLM roles
"""
from __future__ import annotations
import math
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

import yaml

from shared.constants import ROOT_DIR
from shared.utils.logging import get_logger

log = get_logger("providers")

DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "providers.yaml"
LLM_ROLES = ("story", "edit_intent", "translate")

# How long one call may wait on a provider before it counts as failed and the
# chain moves on. Without a limit a hung provider holds the job forever while
# the worker's heartbeat keeps it looking alive (F1 in docs/REQUEST_TRACE.md).
# A provider's `timeout_s` in providers.yaml overrides these.
DEFAULT_TIMEOUT_S: Dict[str, float] = {
    # Gemini scripts that succeeded took 31.5-114.5 s on 2026-10-28, so a
    # minute would have sent most of them to the next provider.
    "story": 180.0,
    "edit_intent": 60.0,
    "translate": 60.0,
    "image": 120.0,
    "tts": 60.0,
}
FALLBACK_TIMEOUT_S = 120.0   # roles not listed above (video, lipsync, music)


def default_timeout_s(role: str) -> float:
    return DEFAULT_TIMEOUT_S.get(role, FALLBACK_TIMEOUT_S)


def _timeout_from(role: str, entry: Dict[str, Any]) -> float:
    """A provider's timeout_s, checked when the config loads rather than at the first call."""
    value = entry.get("timeout_s", default_timeout_s(role))
    # bool is an int in Python, and NaN fails every comparison, so both need saying.
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"providers config: {role} / {entry.get('provider')}: timeout_s "
                         f"must be a number of seconds above 0, not {value!r}")
    return float(value)


@dataclass(frozen=True)
class ProviderSpec:
    """One entry in a role's chain."""
    role: str
    provider: str
    model: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    requires: tuple = ()
    concurrency: int = 1
    retries: int = 1          # attempts before moving to the next provider
    timeout_s: float = 60.0   # longest one call may wait; see DEFAULT_TIMEOUT_S

    @property
    def missing_env(self) -> List[str]:
        """Required env vars that aren't set ("A|B" means either will do)."""
        missing = []
        for requirement in self.requires:
            if not any(os.getenv(name.strip()) for name in str(requirement).split("|")):
                missing.append(str(requirement))
        return missing

    @property
    def available(self) -> bool:
        return not self.missing_env

    def __str__(self) -> str:
        return f"{self.provider}" + (f" ({self.model})" if self.model else "")


class ProviderConfig:
    def __init__(self, data: Dict[str, Any], source: Path | str = "<memory>"):
        self.source = str(source)
        self._roles: Dict[str, List[ProviderSpec]] = {}
        for role, entries in (data.get("roles") or {}).items():
            specs = []
            for entry in entries or []:
                requires = entry.get("requires") or []
                specs.append(ProviderSpec(
                    role=role,
                    provider=entry["provider"],
                    model=entry.get("model"),
                    params=dict(entry.get("params") or {}),
                    requires=tuple(requires if isinstance(requires, list) else [requires]),
                    concurrency=int(entry.get("concurrency", 1)),
                    retries=max(1, int(entry.get("retries", 1))),
                    timeout_s=_timeout_from(role, entry),
                ))
            self._roles[role] = specs

    def roles(self) -> List[str]:
        return list(self._roles)

    def timeout_s(self, role: str, provider: str) -> float:
        """The limit for one provider, for callers that pick providers themselves
        (the TTS tool walks its own engine ladder rather than the chain)."""
        spec = next((s for s in self._roles.get(role, []) if s.provider == provider), None)
        return spec.timeout_s if spec else default_timeout_s(role)

    def specs(self, role: str) -> List[ProviderSpec]:
        """Everything configured for a role, regardless of credentials or overrides."""
        return list(self._roles.get(role, []))

    def chain(self, role: str, include_unavailable: bool = False) -> List[ProviderSpec]:
        """Providers to try for `role`, best first, skipping ones missing credentials."""
        specs = self._roles.get(role, [])
        forced = self._forced_provider(role)
        if forced:
            specs = [s for s in specs if s.provider == forced] or \
                [ProviderSpec(role=role, provider=forced, timeout_s=default_timeout_s(role))]
        return specs if include_unavailable else [s for s in specs if s.available]

    def active(self, role: str) -> Optional[ProviderSpec]:
        """The provider a role will actually use right now."""
        chain = self.chain(role)
        return chain[0] if chain else None

    def concurrency(self, role: str) -> int:
        spec = self.active(role)
        return max(1, spec.concurrency) if spec else 1

    @staticmethod
    def _forced_provider(role: str) -> Optional[str]:
        override = os.getenv(f"PROVIDER_{role.upper()}")
        if override:
            return override.strip()
        legacy = os.getenv("LLM_PROVIDER")
        if legacy and role in LLM_ROLES:
            # "mock" used to mean "no LLM"; the offline provider is named per role.
            return "mymemory" if (legacy == "mock" and role == "translate") else legacy.strip()
        return None


_lock = threading.Lock()
_config: Optional[ProviderConfig] = None
_loaded_from: Optional[str] = None


def load(path: Optional[Path | str] = None, force: bool = False) -> ProviderConfig:
    """Load (and cache) the provider config."""
    global _config, _loaded_from
    target = str(path or os.getenv("PROVIDERS_FILE") or DEFAULT_CONFIG_PATH)
    with _lock:
        if _config is None or force or target != _loaded_from:
            data = yaml.safe_load(Path(target).read_text(encoding="utf-8")) or {}
            _config = ProviderConfig(data, source=target)
            _loaded_from = target
            log.info("provider config loaded from %s (%d roles)", target, len(_config.roles()))
    return _config


def chain(role: str, include_unavailable: bool = False) -> List[ProviderSpec]:
    return load().chain(role, include_unavailable=include_unavailable)


def active(role: str) -> Optional[ProviderSpec]:
    return load().active(role)


def concurrency(role: str) -> int:
    return load().concurrency(role)


def timeout_s(role: str, provider: str) -> float:
    return load().timeout_s(role, provider)


def is_timeout(error: Optional[BaseException]) -> bool:
    """True when a call gave up waiting on its provider, whichever library raised it.

    requests, httpx, the httpx copy inside the OpenAI SDK, and subprocess each
    have their own timeout class, often wrapped in another error, so this walks
    the chain of causes and matches TimeoutError or a class named *Timeout*.
    An HTTP 504 answer is not one: the provider did reply.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, TimeoutError) or "Timeout" in type(error).__name__:
            return True
        error = error.__cause__ or error.__context__
    return False


def http_status(error: Optional[BaseException]) -> Optional[int]:
    """The HTTP status a provider answered with, if it answered at all.

    The class name alone hides it: google-genai raises ServerError for every
    5xx, so an overloaded Gemini (503) and one that ran out of time (504) look
    the same. Each library keeps the number somewhere else, and Dastango's own
    image adapters put it at the start of the message ("HTTP 429: ..."), so
    this checks each in turn, along the chain of causes. Only the number is
    kept, never the message.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        for value in (getattr(error, "status_code", None),                 # openai, anthropic
                      getattr(error, "code", None),                        # google-genai
                      getattr(getattr(error, "response", None), "status_code", None)):  # requests
            if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
                return value
        match = re.match(r"HTTP (\d{3})\b", str(error))
        if match:
            return int(match.group(1))
        error = error.__cause__ or error.__context__
    return None


# ---- daily quotas (DST-20) --------------------------------------------------
#
# A 429 means either "slow down for a few seconds" or "today's allowance is
# gone". Only the second is final until the daily reset, so only the second
# skips the retries. The rule: HTTP 429 AND the provider's message names a
# daily window. The words each provider uses:
#   Cloudflare  "you have used up your daily free allocation of 10,000 neurons"
#               (measured: 41 times in the DST-10 baseline renders, 2026-10-28/29)
#   Gemini      a quotaId containing "PerDay" in the RESOURCE_EXHAUSTED details;
#               its per-minute limit says "PerMinute" and the message is the same
#   Groq        "tokens per day (TPD)", "requests per day (RPD)"; its per-minute
#               answer (measured) says "tokens per minute (TPM)"
#   OpenRouter  "free-models-per-day"
#   MyMemory    "... ALL AVAILABLE FREE TRANSLATIONS FOR TODAY"
# Not "today" on its own: Groq's measured per-minute answer ends "Upgrade to Dev
# Tier today". Any other 429, including one with no message, keeps its retries:
# one retry too many on a daily quota costs a second, but skipping the retries
# on a short limit costs an image.
_DAILY_QUOTA = re.compile(r"per[\s_-]?day|daily|for today|\b(?:TPD|RPD)\b", re.IGNORECASE)

# Which providers ran out of daily quota during the current job ("role:provider").
# Every model call goes through tracing.model_call, which notes it here; the
# orchestrator puts the list on the job's completion event.
_daily_quota_used_up: ContextVar[Optional[Set[str]]] = ContextVar("daily_quota_used_up",
                                                                  default=None)


def is_daily_quota(error: Optional[BaseException]) -> bool:
    """True when a provider refused because today's allowance is used up (rule above)."""
    if http_status(error) != 429:
        return False
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if _DAILY_QUOTA.search(str(error)):
            return True
        error = error.__cause__ or error.__context__
    return False


@contextmanager
def noting_daily_quotas() -> Iterator[Set[str]]:
    """Collect the daily-quota refusals made inside the block, run_jobs threads included."""
    used_up = _daily_quota_used_up.get()
    if used_up is not None:       # already inside a job: share its record
        yield used_up
        return
    used_up = set()
    token = _daily_quota_used_up.set(used_up)
    try:
        yield used_up
    finally:
        _daily_quota_used_up.reset(token)


def note_daily_quota(role: str, provider: str) -> None:
    used_up = _daily_quota_used_up.get()
    if used_up is not None:
        used_up.add(f"{role}:{provider}")


def daily_quotas_used_up() -> List[str]:
    return sorted(_daily_quota_used_up.get() or ())


def describe() -> Dict[str, List[Dict[str, Any]]]:
    """Every configured provider per role, with why it is or isn't usable.

    Shows the full list even when an override pins one provider, so
    `main.py providers` always says what the alternatives are waiting for.
    """
    cfg = load()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for role in cfg.roles():
        forced = cfg._forced_provider(role)
        rows = []
        for spec in cfg.specs(role):
            missing = spec.missing_env
            if forced and spec.provider != forced:
                missing = missing or [f"overridden by {forced}"]
            rows.append({
                "provider": spec.provider,
                "model": spec.model,
                "available": not missing,
                "missing": missing,
            })
        out[role] = rows
    return out
