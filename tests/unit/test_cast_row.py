"""DST-29: the cast on the storyboard.

Planning now draws one portrait per character, with the appearance lock and seed
the render uses, so the storyboard can show who is in the film before anything
is spent on a render, and the render keeps those portraits instead of paying for
them twice. The scenes name who speaks in them. A portrait that cannot be drawn
leaves the storyboard working, with a stand-in the interface labels as such.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import PipelineOrchestrator
from agents.orchestrator.storyboard_view import cast, image_budget, scene_cards
from agents.story_agent.planner import template_script
from agents.video_agent import VideoAgent
from mcp.base_tool import ToolResult
from shared.schemas.pipeline import PipelineState
from shared.schemas.storyboard import Storyboard
from state_manager.state_manager import StateManager
from state_manager.storage import VersionStore
from tests.conftest import signed_in_client

W, H = 320, 180


@pytest.fixture(autouse=True)
def _isolated(isolated_dirs):
    """Plans write portraits under the outputs folder: keep them out of data/."""


@pytest.fixture
def orch(isolated_dirs):
    return PipelineOrchestrator(state_manager=StateManager(VersionStore(isolated_dirs / "state.db")))


def _plan(orch, **kw):
    return orch.plan("A lighthouse keeper befriends a stranded whale", target_duration_s=24,
                     scene_count=3, preview_width=W, preview_height=H, **kw)


def _spy(agent: VideoAgent, fail: bool = False) -> list:
    """Record every image the agent asks for; with `fail`, none can be drawn."""
    prompts: list = []
    real = agent.tools.execute

    def execute(name, **kwargs):
        if name == "vision.generate_image":
            prompts.append(kwargs["prompt"])
            if fail:
                return ToolResult(success=False, error="every image provider is down")
        return real(name, **kwargs)

    agent.tools.execute = execute
    return prompts


def _portrait_calls(prompts) -> list:
    return [p for p in prompts if p.startswith("close-up portrait of")]


# ---- the storyboard carries the cast ----------------------------------------------

def test_planning_draws_one_portrait_per_character_and_records_it(orch):
    state = _plan(orch)
    people = state.script.characters.characters
    assert {p.character_id for p in state.storyboard.portraits} == {c.id for c in people}
    for p in state.storyboard.portraits:
        assert p.signature and Path(p.path).is_file()
        assert Path(p.path).parent.name == "portraits"       # where the render keeps them


def test_the_storyboard_answer_has_the_cast_with_portraits(orch, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes

    state = _plan(orch)
    monkeypatch.setattr(pipeline_routes, "sm", orch.sm)
    client = signed_in_client(app_module.app)
    body = client.get(f"/api/pipeline/storyboard/{state.project_id}").json()

    people = state.script.characters.characters
    assert [m["character_id"] for m in body["cast"]] == [c.id for c in people]
    for member, person in zip(body["cast"], people):
        assert member["name"] == person.name and member["role"] == person.role
        assert member["description"] == person.description
        assert member["voice"]                                   # who will voice them
        assert member["drawn"] is True
        assert member["portrait_url"].startswith(f"/assets/{state.project_id}/video/portraits/")
        assert "?v=" in member["portrait_url"]                   # redrawn in place: cache-busted
        assert client.get(member["portrait_url"]).status_code == 200

    # The scenes, as the page reads them, say who speaks in each (a reload must
    # show what the stream showed).
    for frame, card in zip(body["frames"], scene_cards(state.script)):
        assert frame["characters"] == card["characters"] and frame["characters"]


def test_the_plan_streams_each_portrait_as_it_is_drawn(orch):
    events = []
    state = _plan(orch, on_event=events.append)
    portraits = [e for e in events if (e.phase, e.status) == ("storyboard", "portrait")]
    people = state.script.characters.characters

    assert {e.payload["character_id"] for e in portraits} == {c.id for c in people}
    assert all(e.payload["drawn"] is True and e.payload["portrait_url"].startswith("/assets/")
               for e in portraits)
    # The cast is announced with the script, before any picture exists...
    script = next(e for e in events if (e.phase, e.status) == ("storyboard", "script"))
    assert [m["character_id"] for m in script.payload["cast"]] == [c.id for c in people]
    assert all(m["portrait_url"] is None for m in script.payload["cast"])
    # ...and every portrait lands before the plan is complete.
    done = next(i for i, e in enumerate(events) if (e.phase, e.status) == ("storyboard", "complete"))
    assert all(events.index(e) < done for e in portraits)


def test_the_image_budget_counts_portraits_in_the_plan_not_the_render():
    # DST-36: the render draws no picture the plan did not, unless a scene is long
    # enough to get a detail cutaway. These 8 s scenes are not (see the long case in
    # test_dst36_coverage.py).
    script = template_script("p", "A robot learns to paint", target_duration_s=24, scene_count=3)
    people = len(script.characters.characters)
    assert image_budget(script) == {"plan": 3 + people, "render": 0}


# ---- who is in each scene ---------------------------------------------------------

def test_each_scene_lists_the_characters_who_speak_in_it_in_order():
    script = template_script("p", "A lighthouse keeper befriends a stranded whale",
                             target_duration_s=24, scene_count=3)
    names = {c.id: c.name for c in script.characters.characters}
    cards = scene_cards(script)
    assert len(cards) == len(script.scenes)
    for card, scene in zip(cards, script.scenes):
        expected = []
        for line in scene.dialogue:
            if line.character_id not in expected:
                expected.append(line.character_id)
        assert [c["character_id"] for c in card["characters"]] == expected
        assert [c["name"] for c in card["characters"]] == [names[i] for i in expected]
        assert expected                                          # the template never leaves one empty


def test_a_speaker_missing_from_the_cast_is_left_out_rather_than_crashing():
    script = template_script("p", "A robot learns to paint", target_duration_s=24, scene_count=2)
    script.scenes[0].dialogue[0].character_id = "char_ghost"
    assert "char_ghost" not in [c["character_id"] for c in scene_cards(script)[0]["characters"]]


def _narrator_only(scenes: int = 3):
    script = template_script("p", "A river from a glacier to the sea", target_duration_s=24,
                             scene_count=scenes)
    narrator = next(c for c in script.characters.characters if c.role == "narrator")
    script.characters.characters = [narrator]
    for scene in script.scenes:
        for line in scene.dialogue:
            line.character_id = narrator.id
    return script, narrator


def test_a_narrator_only_film_has_a_one_member_cast_and_every_scene_names_the_narrator(orch):
    script, narrator = _narrator_only()
    assert [m["character_id"] for m in cast(script)] == [narrator.id]
    assert all([c["character_id"] for c in card["characters"]] == [narrator.id]
               for card in scene_cards(script))

    state = PipelineState(project_id="t_narrator", user_prompt="A river")
    state.script = script
    VideoAgent().generate_storyboard(state, width=W, height=H)
    assert [p.character_id for p in state.storyboard.portraits] == [narrator.id]
    assert cast(script, None, state)[0]["portrait_url"]


# ---- a portrait that cannot be drawn ----------------------------------------------

def test_failed_portraits_leave_the_storyboard_working_with_a_stand_in():
    state = PipelineState(project_id="t_failed", user_prompt="A robot learns to paint")
    state.script = template_script("t_failed", state.user_prompt, target_duration_s=24, scene_count=2)
    agent = VideoAgent()
    _spy(agent, fail=True)
    agent.generate_storyboard(state, width=W, height=H)

    assert len(state.storyboard.frames) == 2                     # the scenes are still there
    for stored in state.storyboard.portraits:
        assert Path(stored.path).is_file()                       # something to show
        assert stored.signature is None                          # but never "current"
    members = cast(state.script, None, state)
    assert all(m["drawn"] is False for m in members)
    # The render must therefore draw them properly, not reuse the stand-ins.
    for c in state.script.characters.characters:
        assert agent.reusable_portrait(state, c, W, H) is None


def test_a_failed_redraw_keeps_the_portrait_that_was_already_good():
    state = PipelineState(project_id="t_keep", user_prompt="A robot learns to paint")
    state.script = template_script("t_keep", state.user_prompt, target_duration_s=24, scene_count=2)
    agent = VideoAgent()
    agent.generate_storyboard(state, width=W, height=H)
    hero = state.script.characters.characters[1]
    good = state.storyboard.portrait(hero.id)
    before = Path(good.path).read_bytes()

    _spy(agent, fail=True)
    agent.generate_storyboard(state, width=W, height=H, scene_ids=[], character_ids=[hero.id])
    kept = state.storyboard.portrait(hero.id)
    assert Path(kept.path).read_bytes() == before
    assert kept.signature == good.signature


# ---- the render keeps what the storyboard drew ------------------------------------

def test_the_render_reuses_a_matching_portrait_and_draws_a_stale_one(orch):
    state = _plan(orch)
    agent = orch.video
    prompts = _spy(agent)
    hero = state.script.characters.characters[1]
    stored = state.storyboard.portrait(hero.id).path

    reusable = agent.reusable_portrait(state, hero, W, H)
    assert reusable == stored
    out = agent.generate_portrait(state.project_id, hero, W, H, "", state.script.story, reusable)
    assert _portrait_calls(prompts) == [] and out.image_path == stored

    # Anything the picture depends on changing makes the stored one stale.
    assert agent.reusable_portrait(state, hero, 640, 360) is None           # another size
    hero.image_seed = (hero.image_seed or 0) + 1                            # another seed
    assert agent.reusable_portrait(state, hero, W, H) is None
    hero.image_seed -= 1
    hero.appearance_lock += ", a red scarf"                                 # another look
    assert agent.reusable_portrait(state, hero, W, H) is None
    stale = agent.generate_portrait(state.project_id, hero, W, H, "", state.script.story,
                                    agent.reusable_portrait(state, hero, W, H))
    assert len(_portrait_calls(prompts)) == 1 and Path(stale.image_path).is_file()


def test_a_render_with_a_fresh_plan_draws_no_portrait_again(orch, monkeypatch):
    state = _plan(orch)
    agent = orch.video
    prompts = _spy(agent)
    monkeypatch.setattr(agent, "compose", lambda *_a, **_k: "")      # only the assets matter here
    agent.run(state, width=W, height=H, fps=12, use_text_to_video=False, use_lip_sync=False)

    assert _portrait_calls(prompts) == []                            # kept, not redrawn
    assert len(state.video.portraits) == len(state.script.characters.characters)
    # Only what the plan did not cover is drawn: a medium shot is a crop of the wide
    # picture, so the render draws just the cutaways of the scenes long enough (DST-36).
    assert len(prompts) == image_budget(state.script)["render"]


def test_a_scene_text_edit_never_redraws_the_cast(orch):
    state = _plan(orch)
    prompts = _spy(orch.video)
    scene = state.script.scenes[0]
    line = scene.dialogue[0]
    orch.update_storyboard(state.project_id, scene.scene_id, dialogue={line.line_id: "A new line."})
    assert prompts == []

    orch.update_storyboard(state.project_id, scene.scene_id,
                           visual_prompt="a lighthouse in a storm, wide shot")
    assert len(prompts) == 1 and not _portrait_calls(prompts)        # one scene picture, no faces


# ---- older projects and finished films --------------------------------------------

def test_a_storyboard_saved_before_portraits_existed_still_loads_and_shows_stand_ins():
    old = Storyboard.model_validate({"project_id": "t_old", "prompt": "p", "frames": []})
    assert old.portraits == []
    state = PipelineState(project_id="t_old", user_prompt="p")
    state.script = template_script("t_old", "p", target_duration_s=24, scene_count=2)
    state.storyboard = old
    members = cast(state.script, None, state)
    assert all(m["portrait_url"] is None and m["drawn"] is False for m in members)


def test_the_cast_of_a_finished_film_shows_the_film_s_own_portraits(small_project):
    state, _sm = small_project()
    members = cast(state.script, None, state)
    film = {p.character_id: p.image_path for p in state.video.portraits}
    assert members and all(m["drawn"] is True for m in members)
    for m in members:
        assert Path(film[m["character_id"]]).name in m["portrait_url"]


# ---- who may see a portrait --------------------------------------------------------

def test_another_account_cannot_fetch_a_portrait(orch, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes

    state = _plan(orch)
    monkeypatch.setattr(pipeline_routes, "sm", orch.sm)
    owner = signed_in_client(app_module.app, "saad@example.com")
    monkeypatch.setenv("ALLOW_SIGNUPS", "1")
    url = owner.get(f"/api/pipeline/storyboard/{state.project_id}").json()["cast"][0]["portrait_url"]
    assert owner.get(url).status_code == 200

    mallory = TestClient(app_module.app)
    registered = mallory.post("/api/auth/register",
                              json={"email": "mallory@example.com", "password": "another-decent-passphrase"})
    assert registered.status_code == 200, registered.text
    # 404, not 403: a different answer would confirm the project exists.
    assert mallory.get(url).status_code == 404
    assert mallory.get(f"/api/pipeline/storyboard/{state.project_id}").status_code == 404
    assert TestClient(app_module.app).get(url).status_code == 401           # signed out
