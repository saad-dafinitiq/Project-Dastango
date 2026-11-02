"""DST-25: editing "what the camera sees" must change that scene's picture, honestly.

What was wrong: the picture's address never changed, so the page kept showing the
old one; and when every image provider failed the edit still answered 200, kept the
old picture and stamped it as matching the new text, so the render reused a stale
image. The offline fallback could also overwrite a good preview with a gradient.
"""
from __future__ import annotations

import hashlib
import socket
import textwrap
from pathlib import Path

import pytest

import jobs
from agents.orchestrator import PipelineOrchestrator
from shared import providers
from shared.assets import asset_url
from state_manager.state_manager import StateManager
from state_manager.storage import SqliteStorage
from tests.conftest import signed_in_client, silence_tts

NEW_LOOK = "a vaulted library at 3am, shelves sliding into a map"
W, H = 64, 36                          # the preview size the plan below uses


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def orchestrator(isolated_dirs):
    sm = StateManager(SqliteStorage(isolated_dirs / "state.db"))
    orch = PipelineOrchestrator(state_manager=sm)
    silence_tts(orch.audio.tools)
    return orch


@pytest.fixture
def use_chain(tmp_path, monkeypatch):
    """Point the image role at a chain written by the test; the default is the
    offline placeholder, which is what the whole suite runs on."""
    def use(kind: str):
        monkeypatch.delenv("PROVIDER_IMAGE", raising=False)
        monkeypatch.setenv("AGNES_API_KEY", "agnes-test-key")        # made up; nothing is sent
        dead = f"http://127.0.0.1:{_closed_port()}/v1"
        tail = "    - provider: placeholder\n" if kind == "fallback" else ""
        path = tmp_path / "providers.yaml"
        path.write_text(textwrap.dedent(f"""
            roles:
              image:
                - provider: agnes
                  model: agnes-image-2.1-flash
                  params: {{tier: 1K, base_url: "{dead}"}}
                  requires: [AGNES_API_KEY]
                  retries: 1
        """) + tail, encoding="utf-8")
        monkeypatch.setenv("PROVIDERS_FILE", str(path))
        providers.load(force=True)

    yield use
    monkeypatch.delenv("PROVIDERS_FILE", raising=False)
    providers.load(force=True)


def plan(orch):
    return orch.plan("A night librarian finds the books rearranging into a map",
                     target_duration_s=24, scene_count=3, preview_width=W, preview_height=H)


class ImageCalls:
    """Counts the image requests an orchestrator makes."""

    def __init__(self, orch, monkeypatch):
        self.paths = []
        real = orch.video.tools.execute

        def counting(tool, **kwargs):
            if tool == "vision.generate_image":
                self.paths.append(Path(kwargs.get("out_path", "")).name)
            return real(tool, **kwargs)

        monkeypatch.setattr(orch.video.tools, "execute", counting)


# ---- an edit that works ------------------------------------------------------------

def test_a_visual_edit_draws_exactly_one_new_picture_and_leaves_the_others_alone(
        orchestrator, monkeypatch):
    state = plan(orchestrator)
    before = {f.scene_id: (sha(f.preview_path), f.preview_signature) for f in state.storyboard.frames}
    calls = ImageCalls(orchestrator, monkeypatch)
    outcome = {}

    updated = orchestrator.update_storyboard(state.project_id, "scene_2",
                                             visual_prompt=NEW_LOOK, outcome=outcome)

    assert len(calls.paths) == 1, calls.paths
    assert outcome["redraw"] == {"scene_id": "scene_2", "ok": True}
    after = {f.scene_id: (sha(f.preview_path), f.preview_signature) for f in updated.storyboard.frames}
    assert after["scene_2"][0] != before["scene_2"][0], "the picture changed"
    assert after["scene_2"][1] != before["scene_2"][1], "and its signature moved with it"
    assert after["scene_1"] == before["scene_1"] and after["scene_3"] == before["scene_3"]


@pytest.mark.parametrize("what", ["title", "dialogue"])
def test_changing_only_words_draws_nothing(orchestrator, monkeypatch, what):
    state = plan(orchestrator)
    if what == "title":
        edit = {"title": "A new title"}
    else:
        edit = {"dialogue": {state.script.scenes[0].dialogue[0].line_id: "Different words."}}
    calls = ImageCalls(orchestrator, monkeypatch)
    outcome = {}
    orchestrator.update_storyboard(state.project_id, "scene_1", outcome=outcome, **edit)
    assert calls.paths == [] and outcome == {}


def test_every_edit_is_a_new_version_in_the_history(orchestrator):
    state = plan(orchestrator)
    pid = state.project_id
    before = len(orchestrator.sm.history(pid))
    orchestrator.update_storyboard(pid, "scene_2", visual_prompt=NEW_LOOK)
    history = orchestrator.sm.history(pid)
    assert len(history) == before + 1
    assert history[-1]["description"] == "storyboard edit: scene_2"


def test_the_render_uses_the_new_picture_not_the_old_one(orchestrator):
    from agents.video_agent import VideoAgent
    state = plan(orchestrator)
    old = sha(state.storyboard.frame("scene_2").preview_path)
    updated = orchestrator.update_storyboard(state.project_id, "scene_2", visual_prompt=NEW_LOOK)
    scene = next(s for s in updated.script.scenes if s.scene_id == "scene_2")
    frame = updated.storyboard.frame("scene_2")

    reusable = VideoAgent().reusable_preview(updated, scene, W, H)
    assert reusable == frame.preview_path
    assert sha(reusable) != old and scene.visual_prompt == NEW_LOOK


# ---- an edit that cannot draw ------------------------------------------------------

@pytest.mark.parametrize("chain", ["failing", "fallback"])
def test_a_failed_redraw_keeps_the_old_picture_but_saves_the_words(
        orchestrator, use_chain, chain):
    """"failing": every provider is down. "fallback": the chain ends in the placeholder
    like the shipped one, which used to overwrite the good picture with a gradient."""
    from agents.video_agent import VideoAgent
    state = plan(orchestrator)
    pid = state.project_id
    old = state.storyboard.frame("scene_2")
    old_hash, old_sig, old_path = sha(old.preview_path), old.preview_signature, old.preview_path
    versions = len(orchestrator.sm.history(pid))

    use_chain(chain)
    outcome = {}
    updated = orchestrator.update_storyboard(pid, "scene_2", visual_prompt=NEW_LOOK, outcome=outcome)

    assert outcome["redraw"] == {"scene_id": "scene_2", "ok": False}
    frame = updated.storyboard.frame("scene_2")
    assert frame.preview_path == old_path and sha(frame.preview_path) == old_hash, \
        "the previous picture is still there, untouched"
    assert frame.preview_signature == old_sig, "and it is not claimed to match the new text"
    scene = next(s for s in updated.script.scenes if s.scene_id == "scene_2")
    assert scene.visual_prompt == NEW_LOOK and frame.visual_prompt == NEW_LOOK, "the words are saved"
    # So the render draws the scene from the new text instead of reusing the stale picture.
    assert VideoAgent().reusable_preview(updated, scene, W, H) is None
    history = orchestrator.sm.history(pid)
    assert len(history) == versions + 1 and "picture not redrawn" in history[-1]["description"]
    assert not list(Path(old_path).parent.glob("*.new.png")), "no half-drawn file is left behind"


def test_a_scene_that_never_got_a_picture_is_not_marked_as_having_one(orchestrator, use_chain):
    """Planning while every provider is down: the card gets a placeholder to show, but
    the render must still draw the scene properly."""
    from agents.video_agent import VideoAgent
    use_chain("fallback")
    state = plan(orchestrator)
    frame = state.storyboard.frame("scene_1")
    assert frame.preview_path and Path(frame.preview_path).exists()
    assert frame.preview_signature is None
    scene = next(s for s in state.script.scenes if s.scene_id == "scene_1")
    assert VideoAgent().reusable_preview(state, scene, W, H) is None


def test_the_offline_placeholder_counts_when_it_is_the_provider_you_chose(orchestrator):
    """With no keys the placeholder is the intended provider, not a failure."""
    state = plan(orchestrator)
    frame = state.storyboard.frame("scene_1")
    assert frame.preview_signature


def test_a_scene_can_be_redrawn_once_the_providers_are_back(orchestrator, use_chain, monkeypatch):
    state = plan(orchestrator)
    use_chain("failing")
    orchestrator.update_storyboard(state.project_id, "scene_2", visual_prompt=NEW_LOOK)
    monkeypatch.delenv("PROVIDERS_FILE")
    providers.load(force=True)
    monkeypatch.setenv("PROVIDER_IMAGE", "placeholder")
    outcome = {}
    updated = orchestrator.update_storyboard(state.project_id, "scene_2",
                                             visual_prompt=NEW_LOOK + ", at dawn", outcome=outcome)
    assert outcome["redraw"]["ok"] is True
    assert updated.storyboard.frame("scene_2").preview_signature


def test_the_image_result_says_when_it_came_from_a_fallback(use_chain, tmp_path):
    from mcp.tools.vision_tools.image_gen_tool import ImageGenTool
    use_chain("fallback")
    res = ImageGenTool().run(prompt="a lighthouse", out_path=str(tmp_path / "a.png"), width=64, height=36)
    assert res.success and res.metadata["provider"] == "placeholder" and res.metadata["fallback"] is True


def test_the_chosen_placeholder_is_not_called_a_fallback(tmp_path):
    from mcp.tools.vision_tools.image_gen_tool import ImageGenTool
    res = ImageGenTool().run(prompt="a lighthouse", out_path=str(tmp_path / "a.png"), width=64, height=36)
    assert res.metadata["provider"] == "placeholder" and res.metadata["fallback"] is False


# ---- the address the page uses -----------------------------------------------------

def test_a_versioned_address_changes_when_the_file_does_and_not_otherwise(isolated_dirs):
    from shared import constants
    path = constants.OUTPUTS_DIR / "proj" / "video" / "storyboard" / "scene_1.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"one")
    plain = asset_url(path)
    first = asset_url(path, versioned=True)
    assert first.startswith(plain + "?v=") and asset_url(path, versioned=True) == first
    path.write_bytes(b"two, a different picture")
    import os
    os.utime(path, ns=(10**18, 10**18))        # a clock too coarse to tell two writes apart
    assert asset_url(path, versioned=True) != first
    assert asset_url(path) == plain, "the plain address is unchanged for every other caller"
    assert asset_url(constants.OUTPUTS_DIR / "proj" / "gone.png", versioned=True) \
        == asset_url(constants.OUTPUTS_DIR / "proj" / "gone.png")


@pytest.fixture
def api(isolated_dirs, monkeypatch, orchestrator):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from backend.services import pipeline_service
    monkeypatch.setattr(pipeline_service, "_orchestrator", orchestrator)
    monkeypatch.setattr(pipeline_routes, "sm", orchestrator.sm)
    return signed_in_client(app_module.app)


def test_the_picture_address_changes_only_when_the_picture_does(api, orchestrator, use_chain):
    pid = plan(orchestrator).project_id

    def url(scene="scene_2"):
        board = api.get(f"/api/pipeline/storyboard/{pid}").json()
        return next(f for f in board["frames"] if f["scene_id"] == scene)["preview_url"]

    first = url()
    assert "?v=" in first
    assert api.patch(f"/api/pipeline/storyboard/{pid}/scene_2", json={"title": "Words only"}).status_code == 200
    assert url() == first, "words changed, picture did not"

    assert api.patch(f"/api/pipeline/storyboard/{pid}/scene_2", json={"visual_prompt": NEW_LOOK}).status_code == 200
    redrawn = url()
    assert redrawn != first and redrawn.split("?")[0] == first.split("?")[0], \
        "same file, new version: the page has a reason to fetch it again"
    assert url("scene_1") == url("scene_1")

    use_chain("failing")
    api.patch(f"/api/pipeline/storyboard/{pid}/scene_2", json={"visual_prompt": NEW_LOOK + ", later"})
    assert url() == redrawn, "a failed redraw leaves the picture, and its address, as they were"


def test_the_answer_says_whether_the_picture_was_redrawn(api, orchestrator, use_chain):
    pid = plan(orchestrator).project_id
    words = api.patch(f"/api/pipeline/storyboard/{pid}/scene_2", json={"title": "Words only"}).json()
    assert "redraw" not in words

    drawn = api.patch(f"/api/pipeline/storyboard/{pid}/scene_2", json={"visual_prompt": NEW_LOOK})
    assert drawn.status_code == 200 and drawn.json()["redraw"] == {"scene_id": "scene_2", "ok": True}

    use_chain("fallback")
    failed = api.patch(f"/api/pipeline/storyboard/{pid}/scene_2",
                       json={"visual_prompt": "something quite different again"})
    body = failed.json()
    assert failed.status_code == 200 and body["redraw"]["ok"] is False
    assert "saved" in body["redraw"]["message"] and "previous picture" in body["redraw"]["message"]
    assert "agnes" not in body["redraw"]["message"].lower(), "no provider is named to the creator"
    assert next(f for f in body["frames"] if f["scene_id"] == "scene_2")["visual_prompt"] \
        == "something quite different again"


def test_an_edit_is_refused_while_the_film_is_being_worked_on(api, orchestrator):
    pid = plan(orchestrator).project_id
    job = jobs.enqueue("render", pid, {})
    busy = api.patch(f"/api/pipeline/storyboard/{pid}/scene_2", json={"visual_prompt": NEW_LOOK})
    assert busy.status_code == 409 and "render" in busy.json()["detail"]
    jobs.cancel(job.id)
    assert api.patch(f"/api/pipeline/storyboard/{pid}/scene_2",
                     json={"visual_prompt": NEW_LOOK}).status_code == 200


def test_an_unknown_scene_is_still_a_404(api, orchestrator):
    pid = plan(orchestrator).project_id
    assert api.patch(f"/api/pipeline/storyboard/{pid}/scene_99", json={"title": "x"}).status_code == 404
