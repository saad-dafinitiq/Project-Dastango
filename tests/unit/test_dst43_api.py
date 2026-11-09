"""DST-43 (L3): the API answers 400 for a wrong value and 404 only for what depends on the film,
refuses to go back to a draft with no film, and its reference lists every route."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import signed_in_client

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def api(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import edit as edit_routes
    from backend.routes import history as history_routes
    from backend.routes import pipeline as pipeline_routes

    state, sm = small_project(project_id="t_d43")
    for module in (pipeline_routes, edit_routes, history_routes):
        monkeypatch.setattr(module, "sm", sm)
    return state, sm, signed_in_client(app_module.app)


WRONG_VALUES = [
    ("an unknown mood", "set_music", {"mood": "grumpy"}),
    ("a transition length below the least", "set_transition", {"scene": "2", "transition": "fade", "ms": 99}),
    ("a transition length above the most", "set_transition", {"scene": "2", "transition": "fade", "ms": 1501}),
    ("an unknown transition", "set_transition", {"scene": "2", "transition": "teleport"}),
    ("an unknown effect", "set_effect", {"effect": "sparkle"}),
    ("an unknown strength", "set_effect", {"effect": "grain", "strength": "extreme"}),
    ("an unknown ambience", "set_ambience", {"ambience": "thunder"}),
    ("a level past the top", "set_volume", {"target": "voices", "percent": 300}),
    ("an unknown track", "set_music", {"track": "no-such-track"}),
    ("a tone nobody knows", "set_voice", {"character": "narrator", "tone": "gloomy-ish"}),
]


@pytest.mark.parametrize("what, op, params", WRONG_VALUES, ids=[w[0] for w in WRONG_VALUES])
def test_a_wrong_value_is_400_whatever_the_film_is(api, what, op, params):
    _state, _sm, client = api
    one = client.post("/api/edit/t_d43/op", json={"op": op, "params": params})
    assert one.status_code == 400, (what, one.status_code, one.text)
    stack = client.post("/api/edit/t_d43/ops/check", json={"ops": [{"op": op, "params": params}]})
    assert stack.status_code == 400, (what, stack.status_code, stack.text)


def test_what_depends_on_the_film_is_still_404(api):
    _state, _sm, client = api
    for op, params in [("set_transition", {"scene": "9", "transition": "fade"}),
                       ("set_effect", {"scene": "9", "effect": "grain"}),
                       ("remove_scene", {"scene": "9"}),
                       ("set_camera", {"scene": "1", "shot_id": "no-shot", "move": "pan"})]:
        got = client.post("/api/edit/t_d43/op", json={"op": op, "params": params})
        assert got.status_code == 404, (op, got.status_code, got.text)


def test_a_good_change_is_still_accepted(api):
    _state, _sm, client = api
    ok = client.post("/api/edit/t_d43/ops/check",
                     json={"ops": [{"op": "set_transition", "params": {"scene": "2", "transition": "fade", "ms": 600}}]})
    assert ok.status_code == 200 and len(ok.json()["labels"]) == 1


def test_going_back_to_a_draft_with_no_film_is_refused(api):
    state, sm, client = api
    film_version = state.version
    # A draft made before the first render has no video; the film comes after it.
    draft = state.model_copy(deep=True)
    draft.video = None
    sm.snapshot(draft, asset_paths=[], description="storyboard edit: draft")
    sm.snapshot(state.model_copy(deep=True), asset_paths=[], description="edit: noop (video)")
    latest = sm.latest("t_d43").version
    refused = client.post(f"/api/history/t_d43/revert/{film_version + 1}")
    assert refused.status_code == 409 and "storyboard draft" in refused.json()["detail"]
    assert "/film" in refused.json()["detail"]
    assert sm.latest("t_d43").version == latest                # nothing was reverted, no job ran
    assert client.post("/api/history/t_d43/revert/99").status_code == 404


# ---- the reference -----------------------------------------------------------------------------

def _documented() -> set[tuple[str, str]]:
    rows, prefix = set(), ""
    for line in (ROOT / "docs" / "API.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            m = re.search(r"`(/[^`]*)`", line)
            prefix = m.group(1) if m else ""
        row = re.match(r"\|\s*([A-Z][A-Z/ ,]*?)\s*\|\s*`([^`]+)`", line)
        if not row:
            continue
        path = row.group(2).split("?")[0]
        if not (path.startswith("/api") or path in ("/health", "/ready")):
            path = prefix + path
        for method in re.split(r"[ /,]+", row.group(1)):
            if method:
                rows.add((method, re.sub(r"\{[^}]+\}", "{}", path)))
    return rows


def test_the_reference_lists_every_route_and_only_real_ones():
    from backend import app as app_module

    real = {(method.upper(), re.sub(r"\{[^}]+\}", "{}", path))
            for path, methods in app_module.app.openapi()["paths"].items() for method in methods
            if not path.startswith("/classic")}
    documented = _documented()
    assert real - documented == set(), "routes missing from docs/API.md"
    assert documented - real == set(), "docs/API.md lists routes that do not exist"
