"""DST-45: a creator pins films to show on Create. Pins belong to the account, are idempotent, and cannot be placed on
someone else's film."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import signed_in_client


@pytest.fixture
def api(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import projects as project_routes

    state, sm = small_project(project_id="t_d45_pin")
    monkeypatch.setattr(project_routes, "sm", sm)
    return state, signed_in_client(app_module.app), app_module


def _film(client, pid="t_d45_pin"):
    return next(f for f in client.get("/api/projects/").json() if f["project_id"] == pid)


def test_a_film_starts_unpinned_and_pinning_shows_in_the_list(api):
    _state, client, _app = api
    assert _film(client)["pinned"] is False
    assert client.put("/api/projects/t_d45_pin/pin").json() == {"project_id": "t_d45_pin", "pinned": True}
    assert _film(client)["pinned"] is True
    assert client.delete("/api/projects/t_d45_pin/pin").json()["pinned"] is False
    assert _film(client)["pinned"] is False


def test_pinning_twice_is_one_pin_and_unpinning_what_was_never_pinned_is_fine(api):
    from auth import accounts

    _state, client, _app = api
    assert client.delete("/api/projects/t_d45_pin/pin").status_code == 200
    assert client.put("/api/projects/t_d45_pin/pin").status_code == 200
    assert client.put("/api/projects/t_d45_pin/pin").status_code == 200
    assert accounts.pinned_ids(accounts.get_by_email("tester@example.com")) == {"t_d45_pin"}


def test_another_account_cannot_pin_a_film_that_is_not_theirs_and_sees_no_pin(api, monkeypatch):
    _state, admin, app_module = api
    admin.put("/api/projects/t_d45_pin/pin")
    monkeypatch.setenv("ALLOW_SIGNUPS", "1")
    mallory = TestClient(app_module.app)
    assert mallory.post("/api/auth/register", json={"email": "mallory@example.com", "password": "another-decent-passphrase"}).status_code == 200
    # 404, not 403: a different answer would confirm the film exists.
    assert mallory.put("/api/projects/t_d45_pin/pin").status_code == 404
    assert mallory.delete("/api/projects/t_d45_pin/pin").status_code == 404
    assert all(f["project_id"] != "t_d45_pin" for f in mallory.get("/api/projects/").json())
    # and mallory's attempt did not disturb the owner's pin
    assert _film(admin)["pinned"] is True


def test_signed_out_cannot_pin(api):
    _state, _client, app_module = api
    assert TestClient(app_module.app).put("/api/projects/t_d45_pin/pin").status_code == 401
