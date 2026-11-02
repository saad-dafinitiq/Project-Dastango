"""DST-22: Agnes AI as the image backstop behind Cloudflare, Pollinations switched off.

Everything here talks to a local fake of Agnes's API: no network, no key, no quota.
The request shape the fake checks is the one in Agnes's documentation, confirmed
by one live call on 2026-11-02 (docs/EVAL.md, DST-22 section).
"""
from __future__ import annotations
import base64
import json
import textwrap
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
from PIL import Image

from mcp.tools.vision_tools import image_gen_tool
from mcp.tools.vision_tools.image_gen_tool import ImageGenTool, _agnes_ratio
from shared import providers
from tests.unit.test_model_call_tracing import spans  # noqa: F401  (fixture)

TEST_KEY = "agnes-test-key"          # a made-up value; nothing real is ever used in tests


def _png(w=1312, h=736) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (200, 60, 40)).save(buf, format="PNG")
    return buf.getvalue()


class AgnesServer:
    """Answers POST /v1/images/generations from a script of (status, body) replies.

    The last reply repeats once the script runs out. Every request is recorded.
    """

    def __init__(self, script):
        self.script = list(script)
        self.requests: list = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):                      # the image a `url` answer points at
                body = _png(640, 360)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                       "body": json.loads(raw or b"{}")})
                index = min(len(outer.requests), len(outer.script)) - 1
                status, payload = outer.script[index]
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
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


def _ok(png: bytes | None = None) -> tuple:
    b64 = base64.b64encode(png or _png()).decode()
    return 200, {"created": 1, "data": [{"url": None, "b64_json": b64, "revised_prompt": None}]}


@pytest.fixture
def agnes(tmp_path, monkeypatch):
    """A chain of Agnes (against a fake server) then the placeholder; returns a starter."""
    monkeypatch.delenv("PROVIDER_IMAGE", raising=False)
    monkeypatch.setenv("AGNES_API_KEY", TEST_KEY)
    # Retries sleep 1.5 s, 3 s ... Replace the sleep in the tool's own `time`, never
    # the global one: subprocess.wait polls with it (the DST-20 CI failure).
    monkeypatch.setattr(image_gen_tool, "time", types.SimpleNamespace(sleep=lambda s: None))
    started = []

    def start(script, retries=3):
        server = AgnesServer(script)
        started.append(server)
        path = tmp_path / "providers.yaml"
        path.write_text(textwrap.dedent(f"""
            roles:
              image:
                - provider: agnes
                  model: agnes-image-2.1-flash
                  params: {{tier: 1K, base_url: "{server.url}/v1"}}
                  requires: [AGNES_API_KEY]
                  retries: {retries}
                - provider: placeholder
        """), encoding="utf-8")
        monkeypatch.setenv("PROVIDERS_FILE", str(path))
        providers.load(force=True)
        return server

    yield start
    for server in started:
        server.close()
    monkeypatch.delenv("PROVIDERS_FILE", raising=False)
    providers.load(force=True)


def _generate(tmp_path, w=320, h=180):
    return ImageGenTool().run(prompt="a red paper boat on a calm lake", out_path=str(tmp_path / "s.png"),
                              width=w, height=h, seed=7, negative_prompt="blurry")


# ---- success -----------------------------------------------------------------------

def test_agnes_success_is_saved_at_the_requested_size_and_named(tmp_path, agnes):
    server = agnes([_ok()])
    res = _generate(tmp_path)

    assert res.success, res.error
    assert res.metadata["provider"] == "agnes" and res.metadata["model"] == "agnes-image-2.1-flash"
    assert res.metadata["native_size"] == [1312, 736]
    assert Image.open(res.data).size == (320, 180)

    (request,) = server.requests
    assert request["path"] == "/v1/images/generations"
    assert request["auth"] == f"Bearer {TEST_KEY}"
    # The shape Agnes documents: a tier plus a ratio, response_format INSIDE extra_body.
    assert request["body"] == {"model": "agnes-image-2.1-flash",
                               "prompt": "a red paper boat on a calm lake",
                               "size": "1K", "ratio": "16:9",
                               "extra_body": {"response_format": "b64_json"}}


def test_an_answer_with_a_url_instead_of_base64_is_downloaded(tmp_path, agnes):
    server = agnes([(200, {"created": 1, "data": [{"url": "PLACEHOLDER", "b64_json": None}]})])
    server.script[0] = (200, {"created": 1,
                              "data": [{"url": f"{server.url}/img.png", "b64_json": None}]})
    res = _generate(tmp_path)
    assert res.success and res.metadata["provider"] == "agnes"
    assert res.metadata["native_size"] == [640, 360]


@pytest.mark.parametrize("w,h,ratio", [(1280, 720, "16:9"), (320, 180, "16:9"),
                                       (1024, 1024, "1:1"), (720, 1280, "9:16"),
                                       (800, 600, "4:3"), (600, 800, "3:4"),
                                       (1500, 1000, "3:2"), (2000, 800, "21:9")])
def test_the_ratio_is_the_documented_one_closest_to_the_requested_size(w, h, ratio):
    assert _agnes_ratio(w, h) == ratio


# ---- failure -----------------------------------------------------------------------

def test_an_agnes_outage_falls_through_to_the_placeholder_after_its_retries(tmp_path, agnes):
    server = agnes([(500, {"error": "boom"})])
    res = _generate(tmp_path)

    assert res.success and res.metadata["provider"] == "placeholder"
    assert Image.open(res.data).size == (320, 180)
    assert len(server.requests) == 3                  # retries: 3, then the next provider


def test_no_balance_402_is_not_retried(tmp_path, agnes):
    server = agnes([(402, {"error": "insufficient balance"})])
    res = _generate(tmp_path)
    assert res.metadata["provider"] == "placeholder"
    assert len(server.requests) == 1                  # a spent credit won't come back


def test_the_per_minute_429_is_retried_not_treated_as_a_daily_quota(tmp_path, agnes):
    server = agnes([(429, {"error": "rate limit exceeded, retry later"}), _ok()])
    res = _generate(tmp_path)
    assert res.success and res.metadata["provider"] == "agnes"
    assert res.metadata["attempt"] == 2 and len(server.requests) == 2


def test_a_bad_key_401_is_not_retried(tmp_path, agnes):
    server = agnes([(401, {"error": "invalid api key"})])
    assert _generate(tmp_path).metadata["provider"] == "placeholder"
    assert len(server.requests) == 1


@pytest.mark.parametrize("reply", [b"<html>gateway error</html>",
                                   b'{"data": []}',
                                   b'{"data": [{"url": null, "b64_json": null}]}'])
def test_an_answer_that_is_not_an_image_is_never_saved_as_one(tmp_path, agnes, reply):
    agnes([(200, reply)], retries=1)
    res = _generate(tmp_path)
    assert res.metadata["provider"] == "placeholder"


def test_base64_that_is_not_an_image_falls_through(tmp_path, agnes):
    bad = base64.b64encode(b"this is text, not a picture").decode()
    agnes([(200, {"data": [{"b64_json": bad}]})], retries=1)
    assert _generate(tmp_path).metadata["provider"] == "placeholder"


# ---- the key ---------------------------------------------------------------------

def test_without_the_key_agnes_is_skipped_and_no_request_is_made(tmp_path, agnes, monkeypatch):
    server = agnes([_ok()])
    monkeypatch.delenv("AGNES_API_KEY")
    assert [s.provider for s in providers.chain("image")] == ["placeholder"]
    assert _generate(tmp_path).metadata["provider"] == "placeholder"
    assert server.requests == []


# ---- tracing -----------------------------------------------------------------------

def test_the_trace_records_agnes_as_the_provider_and_every_attempt(tmp_path, agnes, spans):  # noqa: F811
    agnes([(500, {"error": "boom"}), _ok()])
    _generate(tmp_path)
    mine = [s for s in spans.spans if s["provider"] == "agnes"]
    assert [(s["attempt"], s["ok"], s.get("http_status")) for s in mine] == \
        [(1, False, 500), (2, True, None)]
    assert all(s["kind"] == "image" and s["model"] == "agnes-image-2.1-flash" for s in mine)
    assert TEST_KEY not in json.dumps(spans.spans) and TEST_KEY not in json.dumps(spans.io)


# ---- the shipped chain: Pollinations is off, code kept -----------------------------

@pytest.fixture
def shipped(monkeypatch):
    """The real config/providers.yaml, with Cloudflare and Agnes keys present."""
    monkeypatch.delenv("PROVIDERS_FILE", raising=False)
    monkeypatch.delenv("PROVIDER_IMAGE", raising=False)
    for name in ("LOCAL_SD", "SD_API_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("AGNES_API_KEY", TEST_KEY)
    providers.load(force=True)
    yield
    providers.load(force=True)


def test_the_shipped_image_chain_is_cloudflare_then_agnes_then_placeholder(shipped):
    names = [s.provider for s in providers.chain("image")]
    assert names == ["cloudflare", "agnes", "placeholder"]
    assert "pollinations" not in [s.provider for s in providers.chain("image", include_unavailable=True)]


def test_the_shipped_agnes_entry_has_the_limits_the_ticket_asks_for(shipped):
    agnes_spec = next(s for s in providers.chain("image") if s.provider == "agnes")
    assert agnes_spec.requires == ("AGNES_API_KEY",)
    assert agnes_spec.model == "agnes-image-2.1-flash" and agnes_spec.params["tier"] == "1K"
    assert agnes_spec.timeout_s == 120                # DST-12: its own limit, not the default
    assert agnes_spec.retries == 3 and agnes_spec.concurrency == 2


def test_pollinations_is_never_called_when_everything_before_the_placeholder_fails(
        tmp_path, shipped, monkeypatch):
    import requests

    def pollinations(*args, **kwargs):
        raise AssertionError("Pollinations was called although it is switched off")

    monkeypatch.setattr(ImageGenTool, "_provider_pollinations", pollinations)
    monkeypatch.setattr(image_gen_tool, "time", types.SimpleNamespace(sleep=lambda s: None))
    posts = []

    def refuse(url, **kwargs):
        posts.append(url)
        raise requests.ConnectionError("no network in tests")

    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(requests, "get", refuse)
    res = ImageGenTool().run(prompt="a lighthouse", out_path=str(tmp_path / "s.png"),
                             width=64, height=36)
    assert res.success and res.metadata["provider"] == "placeholder"
    assert any("cloudflare" in u for u in posts) and any("agnes" in u for u in posts)


def test_the_pollinations_adapter_is_still_there_for_anyone_who_switches_it_back_on():
    assert callable(getattr(ImageGenTool, "_provider_pollinations", None))
