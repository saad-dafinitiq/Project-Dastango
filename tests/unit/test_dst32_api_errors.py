"""DST-32: how the API notes a server error, for the monitor and for Langfuse.

What matters is what is NOT recorded: a request that fails must leave behind the
class of the error and the route pattern, and never the URL, the message, a header
or a body (the ticket: no secrets or prompt text in error reports).
"""
from __future__ import annotations
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.request_log import RequestLogMiddleware
from shared import monitoring, tracing


class FakeTracer:
    def __init__(self, fail=False):
        self.events, self.fail = [], fail

    def event(self, fields):
        if self.fail:
            raise RuntimeError("tracing server is down")
        self.events.append(fields)


@pytest.fixture
def events():
    tracing._api_error_times.clear()
    fake = FakeTracer()
    tracing.use(fake)
    yield fake
    tracing.use(None)
    tracing._api_error_times.clear()


SECRET_PROMPT = "a story about my bank password hunter2"


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)

    @app.get("/ok")
    def ok():
        return {"fine": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        return {"status": "ok"}

    @app.post("/boom")
    async def boom(request: Request):
        await request.body()
        raise ValueError(f"cannot write: {SECRET_PROMPT}")

    @app.get("/films/{film_id}")
    def film(film_id: str):
        return JSONResponse({"error": "backend unavailable"}, status_code=503)

    @app.get("/missing")
    def missing():
        return JSONResponse({"error": "no"}, status_code=404)

    # The server errors are returned to the client, as they are in production.
    return TestClient(app, raise_server_exceptions=False)


def statuses(tmp_path):
    return [status for _, status in monitoring.read_requests(__import__("time").time(), 60)]


def test_every_answered_request_is_noted_by_status_only(client, events, tmp_path):
    client.get("/ok")
    client.get("/missing")
    assert statuses(tmp_path) == [200, 404]
    (logfile,) = (tmp_path / "monitoring").glob("requests-*.log")
    for line in logfile.read_text().splitlines():
        assert len(line.split()) == 2          # a time and a status: no path, no address
    assert events.events == []                 # only server errors reach Langfuse


def test_a_films_byte_range_requests_still_stream_through_the_middleware(tmp_path):
    # The player seeks with Range requests; a middleware that buffered the response
    # (Starlette's BaseHTTPMiddleware) would break that, which is why this one is pure ASGI.
    from fastapi.responses import FileResponse
    film = tmp_path / "film.bin"
    film.write_bytes(bytes(range(256)) * 40)
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)

    @app.get("/film")
    def serve():
        return FileResponse(film)

    answer = TestClient(app).get("/film", headers={"Range": "bytes=10-19"})
    assert answer.status_code == 206 and answer.content == bytes(range(10, 20))
    assert answer.headers["content-range"] == f"bytes 10-19/{film.stat().st_size}"
    assert statuses(tmp_path) == [206]


def test_the_probes_are_not_counted(client, events, tmp_path):
    for _ in range(10):
        client.get("/health")
        client.get("/ready")
    assert statuses(tmp_path) == []


def test_an_unhandled_exception_is_a_500_with_the_class_and_route_but_not_the_message(
        client, events, tmp_path):
    response = client.post("/boom", content=SECRET_PROMPT)
    assert response.status_code == 500
    assert statuses(tmp_path) == [500]
    (event,) = events.events
    assert event == {"kind": "api", "error": "ValueError", "route": "/boom",
                     "method": "POST", "http_status": 500}
    assert SECRET_PROMPT not in repr(event) and "hunter2" not in repr(event)


def test_an_error_response_is_recorded_under_its_route_pattern_not_the_url_asked_for(
        client, events, tmp_path):
    client.get("/films/private-film-id-12345?token=abc")
    (event,) = events.events
    assert event["route"] == "/films/{film_id}" and event["error"] == "HTTP503"
    assert "private-film-id-12345" not in repr(event) and "abc" not in repr(event)


def test_a_failing_tracing_server_never_changes_the_answer(client, tmp_path):
    tracing.use(FakeTracer(fail=True))
    tracing._api_error_times.clear()
    try:
        assert client.post("/boom", content="x").status_code == 500
        assert client.get("/films/1").status_code == 503
        assert client.get("/ok").json() == {"fine": True}
    finally:
        tracing.use(None)
        tracing._api_error_times.clear()
    assert statuses(tmp_path) == [500, 503, 200]


def test_a_broken_request_log_never_changes_the_answer(client, events, monkeypatch):
    def broken(status, at=None):
        raise RuntimeError("log disk gone")
    monkeypatch.setattr(monitoring, "log_request", broken)
    assert client.get("/ok").status_code == 200
    assert client.post("/boom", content="x").status_code == 500


def _broken(*args, **kwargs):
    raise RuntimeError("anything")


def test_the_bookkeeping_swallows_a_failing_request_log(monkeypatch):
    from backend import request_log
    monkeypatch.setattr(monitoring, "log_request", _broken)
    request_log._note({"type": "http", "method": "GET"}, 500, "ValueError")   # raises nothing


def test_the_bookkeeping_swallows_a_failing_tracer_call(monkeypatch):
    from backend import request_log
    monkeypatch.setattr(tracing, "record_api_error", _broken)
    request_log._note({"type": "http", "method": "GET"}, 503, None)           # raises nothing


def test_a_loop_of_errors_cannot_flood_the_tracing_server(client, events, tmp_path):
    for _ in range(tracing.API_ERRORS_PER_MINUTE + 25):
        client.get("/films/1")
    assert len(events.events) == tracing.API_ERRORS_PER_MINUTE
    # The monitor still counts every one of them.
    assert statuses(tmp_path).count(503) == tracing.API_ERRORS_PER_MINUTE + 25


def test_the_event_carries_only_the_allow_listed_fields_and_blanks_credentials(monkeypatch, events):
    fake_credential = "x" * 24      # not a real key: only has to look like a credential's value
    monkeypatch.setenv("SOME_API_KEY", fake_credential)
    tracing.record_api_error(fake_credential, "/x/{id}", "GET")
    (event,) = events.events
    assert set(event) <= set(tracing.API_ERROR_FIELDS)
    assert fake_credential not in repr(event) and event["error"] == "***"


def test_langfuse_gets_an_error_level_event_named_api_error(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = []

        def create_event(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    tracing.LangfuseTracer(client, propagate_attributes=None).event(
        {"kind": "api", "error": "ValueError", "route": "/boom", "method": "POST",
         "http_status": 500})
    (call,) = client.calls
    assert call["name"] == "api-error" and call["level"] == "ERROR"
    assert call["status_message"] == "ValueError" and call["metadata"]["route"] == "/boom"


def test_the_noop_tracer_accepts_the_event():
    tracing.NoopTracer().event({"kind": "api"})


def test_an_external_uptime_check_can_ask_for_health_with_get_or_head():
    from backend.app import app
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/health").json() == {"status": "ok"}
    head = client.head("/health")
    assert head.status_code == 200 and head.content == b""


def test_the_real_app_has_the_middleware_and_notes_its_requests(tmp_path):
    from backend.app import app
    assert RequestLogMiddleware in [m.cls for m in app.user_middleware]
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/projects/").status_code == 401      # signed out
    client.get("/health")
    assert statuses(tmp_path) == [401]
