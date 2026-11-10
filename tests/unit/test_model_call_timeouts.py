"""DST-12: every model call has a time limit, and a hung provider falls through.

The behaviour tests use real sockets: SilentServer accepts a connection and never
answers, which is what a hung provider looks like from the client's side.
"""
from __future__ import annotations
import json
import socket
import subprocess
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The adapters import their SDK inside the call, and google.genai alone takes
# ~1 s to load. Importing both here means a test times the wait for the
# provider, not the import.
import google.genai  # noqa: F401
import openai  # noqa: F401
import httpx
import pytest
import requests
from pydantic import BaseModel

from shared import providers
from shared.providers import is_timeout

# Slack on top of a timeout before a test calls it too slow: a loaded CI runner,
# not a second chance for a missing limit.
SLACK_S = 3.0
# How long a client may hold a silent connection past its limit, measured on the server's own clock from
# the moment it accepted the connection to the moment the client hung up (DST-44). That is the client's wait
# and nothing else: the test thread starting, the SDK loading and the machine being busy do not count in it,
# which is what made a wall-clock bound around the whole call fail on a loaded machine.
HOLD_SLACK_S = 1.5


class SilentServer:
    """Accepts every connection, holds it open and never answers."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.connections = 0
        self.waits: list[float] = []        # per connection: seconds from accept to the client hanging up
        self._held = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:        # the 0.2 s accept timeout, so close() is noticed
                continue
            self.connections += 1
            self._held.append(conn)
            threading.Thread(target=self._watch, args=(conn, time.monotonic()), daemon=True).start()

    def _watch(self, conn, accepted_at):
        """Note when the client gives up on this connection."""
        conn.settimeout(0.1)
        while not self._stop.is_set():
            try:
                if conn.recv(65536) == b"":
                    self.waits.append(time.monotonic() - accepted_at)
                    return
            except socket.timeout:
                continue
            except OSError:
                self.waits.append(time.monotonic() - accepted_at)
                return

    def wait_for_hangups(self, count: int, seconds: float = 5.0) -> None:
        """Give the watching threads a moment to see the clients hang up."""
        end = time.monotonic() + seconds
        while len(self.waits) < count and time.monotonic() < end:
            time.sleep(0.05)

    def close(self):
        self._stop.set()
        self._thread.join(2)
        for conn in self._held:
            conn.close()
        self.sock.close()


class ChatServer:
    """Answers any POST like an OpenAI-compatible chat API, after `delay_s`."""

    def __init__(self, reply: str, delay_s: float = 0.0):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                time.sleep(delay_s)
                body = json.dumps({
                    "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
                    "model": "test",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": reply}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def silent():
    server = SilentServer()
    yield server
    server.close()


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """Point the provider config at a YAML written by the test."""
    from mcp.tools.llm_tools.llm_client import reset_clients

    for name in ("LLM_PROVIDER", "PROVIDER_STORY", "PROVIDER_IMAGE", "PROVIDER_TTS",
                 "PROVIDER_TRANSLATE"):
        monkeypatch.delenv(name, raising=False)

    def use(yaml_text: str):
        path = tmp_path / "providers.yaml"
        path.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
        monkeypatch.setenv("PROVIDERS_FILE", str(path))
        reset_clients()
        return providers.load(force=True)

    yield use
    # Back to the shipped config: the test's YAML may be one that cannot load.
    monkeypatch.delenv("PROVIDERS_FILE", raising=False)
    reset_clients()
    providers.load(force=True)


def _within(seconds: float, call):
    """Run call() and return (result, elapsed), failing rather than hanging the suite."""
    outcome = {}

    def target():
        try:
            outcome["result"] = call()
        except BaseException as e:  # noqa: BLE001  (re-raised in the test's thread)
            outcome["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(seconds)
    elapsed = time.monotonic() - started
    if thread.is_alive():
        pytest.fail(f"still waiting after {seconds:g} s: this call has no working time limit")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"], elapsed


class Word(BaseModel):
    word: str


# ---- configuration ------------------------------------------------------------

def test_each_role_has_a_default_and_a_provider_can_override_it(chain):
    config = chain("""
        roles:
          story:     [{provider: gemini}, {provider: groq, timeout_s: 7.5}]
          translate: [{provider: groq}]
          image:     [{provider: pollinations}]
          tts:       [{provider: edge}]
          music:     [{provider: silent}]
    """)
    assert [s.timeout_s for s in config.specs("story")] == [180.0, 7.5]
    assert config.specs("translate")[0].timeout_s == 60.0
    assert config.specs("image")[0].timeout_s == 120.0
    assert config.specs("tts")[0].timeout_s == 60.0
    assert config.specs("music")[0].timeout_s == providers.FALLBACK_TIMEOUT_S
    # Callers that pick providers themselves (the TTS ladder) ask by name.
    assert config.timeout_s("story", "groq") == 7.5
    assert config.timeout_s("tts", "gtts") == 60.0          # not listed: the role default


@pytest.mark.parametrize("value", ["0", "-5", "fast", "true", ".nan", ".inf", "[]"])
def test_a_bad_timeout_is_refused_when_the_config_loads(chain, value):
    with pytest.raises(ValueError, match=r"image / cloudflare: timeout_s must be"):
        chain(f"roles:\n  image: [{{provider: cloudflare, timeout_s: {value}}}]\n")


def test_the_shipped_config_keeps_every_limit_that_existed_before(monkeypatch):
    monkeypatch.delenv("PROVIDERS_FILE", raising=False)
    config = providers.load(force=True)
    assert config.timeout_s("image", "cloudflare") == 180    # was timeout=180 in the code
    assert config.timeout_s("image", "sd_webui") == 300      # was timeout=300
    assert config.timeout_s("image", "pollinations") == 120  # was timeout=120
    assert config.timeout_s("tts", "elevenlabs") == 60       # was timeout=60
    assert config.timeout_s("story", "gemini") == 180        # had none at all


@pytest.mark.parametrize("error,expected", [
    (requests.exceptions.ReadTimeout(), True),
    (requests.exceptions.ConnectTimeout(), True),
    (httpx.ReadTimeout("no data"), True),
    (subprocess.TimeoutExpired(["edge-tts"], 1), True),
    (TimeoutError(), True),
    (requests.exceptions.ConnectionError("refused"), False),
    (RuntimeError("HTTP 504: Gateway Timeout"), False),     # the provider did answer
    (ValueError("bad JSON"), False),
    (None, False),
])
def test_timeouts_are_recognised_whichever_library_raised_them(error, expected):
    assert is_timeout(error) is expected


def test_a_timeout_wrapped_in_another_error_is_still_a_timeout():
    try:
        try:
            raise httpx.ReadTimeout("no data")
        except httpx.ReadTimeout as inner:
            raise RuntimeError("request failed") from inner
    except RuntimeError as outer:
        assert is_timeout(outer)


# ---- LLM providers --------------------------------------------------------------

@pytest.mark.parametrize("structured", [False, True])
def test_a_hung_gemini_falls_through_to_the_next_provider(chain, silent, monkeypatch, structured):
    """The provider that hung for 36 minutes in the 2026-10-28 baseline run."""
    from mcp.tools.llm_tools.llm_client import LLMClient

    answering = ChatServer(reply='{"word": "hello"}')
    try:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", silent.url)   # read by google-genai
        monkeypatch.setenv("OLLAMA_HOST", answering.url)
        chain("""
            roles:
              story:
                - {provider: gemini, model: gemini-flash-latest, requires: [GEMINI_API_KEY], timeout_s: 1}
                - {provider: ollama, model: test, requires: [OLLAMA_HOST], timeout_s: 5}
                - {provider: mock}
        """)
        client = LLMClient("story")
        if structured:
            result, elapsed = _within(20, lambda: client.generate_structured("hi", Word))
            assert result.word == "hello"
        else:
            result, elapsed = _within(20, lambda: client.generate("hi"))
            assert result.provider == "ollama"
    finally:
        answering.close()

    assert silent.connections == 1                  # Gemini really was asked, once
    silent.wait_for_hangups(1)
    assert len(silent.waits) == 1 and 0.9 <= silent.waits[0] < 1.0 + HOLD_SLACK_S   # it waited its limit, and no longer


def test_a_hung_openai_compatible_provider_is_asked_once(chain, silent, monkeypatch):
    """Groq, OpenRouter, Ollama and OpenAI share this client. Its SDK used to wait
    600 s and retry twice on its own before the chain got a say."""
    from mcp.tools.llm_tools.llm_client import LLMClient

    monkeypatch.setenv("OLLAMA_HOST", silent.url)
    chain("""
        roles:
          story:
            - {provider: ollama, model: test, requires: [OLLAMA_HOST], timeout_s: 1}
            - {provider: mock}
    """)
    result, elapsed = _within(20, lambda: LLMClient("story").generate("hi"))
    assert result.provider == "mock"                # fell through to the next one
    assert silent.connections == 1                  # no SDK retries
    silent.wait_for_hangups(1)
    assert len(silent.waits) == 1 and 0.9 <= silent.waits[0] < 1.0 + HOLD_SLACK_S


def test_a_slow_answer_inside_the_limit_is_kept(chain, monkeypatch):
    """A limit must not cut off a provider that is merely slow."""
    from mcp.tools.llm_tools.llm_client import LLMClient

    answering = ChatServer(reply="still here", delay_s=1.0)
    try:
        monkeypatch.setenv("OLLAMA_HOST", answering.url)
        chain("""
            roles:
              story:
                - {provider: ollama, model: test, requires: [OLLAMA_HOST], timeout_s: 4}
                - {provider: mock}
        """)
        result, _ = _within(20, lambda: LLMClient("story").generate("hi"))
    finally:
        answering.close()
    assert (result.provider, result.text) == ("ollama", "still here")


# ---- image providers -------------------------------------------------------------

def test_a_hung_image_provider_falls_through_without_using_its_retries(
        chain, silent, monkeypatch, tmp_path):
    from mcp.tools.vision_tools.image_gen_tool import ImageGenTool

    monkeypatch.setenv("SD_API_URL", silent.url)
    chain("""
        roles:
          image:
            - {provider: sd_webui, requires: [SD_API_URL], timeout_s: 1, retries: 3}
            - {provider: placeholder}
    """)
    result, elapsed = _within(30, lambda: ImageGenTool().run(
        prompt="a lighthouse", out_path=str(tmp_path / "shot.png"), width=64, height=36))

    assert result.success and result.metadata["provider"] == "placeholder"
    assert Path(result.data).exists()
    # One attempt: the checkpoint probe plus the generation request. Retrying the
    # timeout three times would have made six connections and taken ~10 s.
    assert silent.connections == 2
    silent.wait_for_hangups(2)
    assert len(silent.waits) == 2 and all(w < 1.0 + HOLD_SLACK_S for w in silent.waits)   # each one held to its limit


# ---- voices ----------------------------------------------------------------------

def test_a_hung_edge_tts_is_stopped_and_the_next_engine_speaks(
        chain, monkeypatch, tmp_path):
    from mcp.tools.audio_tools import tts_tool

    # A stand-in edge-tts that starts and then never finishes, found ahead of
    # the real package because PYTHONPATH comes before site-packages.
    fake = tmp_path / "fake_modules" / "edge_tts"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("", encoding="utf-8")
    # It notes when it started, so the wait is timed from there on its own clock, not from the test's start
    # (which counts spawning an interpreter, slow on a busy machine; DST-44).
    (fake / "__main__.py").write_text(
        "import os, time\nopen(os.environ['FAKE_EDGE_STARTED'], 'w').write(str(time.time()))\ntime.sleep(30)\n",
        encoding="utf-8")
    started = tmp_path / "edge_started.txt"
    monkeypatch.setenv("FAKE_EDGE_STARTED", str(started))
    monkeypatch.setenv("PYTHONPATH", str(fake.parent))

    def gtts_offline(self, text, out, language="en", tld="com"):
        out = out.with_suffix(".mp3")
        out.write_bytes(b"ID3")
        return out

    monkeypatch.setattr(tts_tool.TtsTool, "_gtts", gtts_offline)
    chain("""
        roles:
          tts:
            - {provider: edge, timeout_s: 1}
            - {provider: gtts}
    """)
    result, elapsed = _within(20, lambda: tts_tool.TtsTool().run(
        text="Hello there.", out_path=str(tmp_path / "line.mp3"), engine="edge"))

    assert result.metadata["engine"] == "gtts"
    assert time.time() - float(started.read_text()) < 1.0 + SLACK_S     # stopped at its limit, not at its 30 s


def test_the_other_online_voices_are_given_their_limits(chain, monkeypatch, tmp_path):
    """gTTS's host and ElevenLabs' URL are fixed, so these check the limit is passed."""
    import gtts
    from mcp.tools.audio_tools import tts_tool

    chain("""
        roles:
          tts:
            - {provider: elevenlabs, timeout_s: 7}
            - {provider: gtts, timeout_s: 9}
    """)
    seen = {}

    class RecordingGTTS:
        def __init__(self, **kwargs):
            seen["gtts"] = kwargs.get("timeout")

        def save(self, path):
            Path(path).write_bytes(b"ID3")

    def recording_post(url, **kwargs):
        seen["elevenlabs"] = kwargs.get("timeout")
        raise requests.exceptions.ReadTimeout()

    monkeypatch.setattr(gtts, "gTTS", RecordingGTTS)
    monkeypatch.setattr(requests, "post", recording_post)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-not-real")

    result = tts_tool.TtsTool().run(text="Hi.", out_path=str(tmp_path / "a.mp3"),
                                    engine="elevenlabs")
    assert seen == {"elevenlabs": 7, "gtts": 9}
    assert result.metadata["engine"] == "gtts"      # the timed-out engine fell through
