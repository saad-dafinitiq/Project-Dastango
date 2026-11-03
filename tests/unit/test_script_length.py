"""DST-28: a script is held to the length asked for.

The model writes the script, the timeline's own estimate says how long it plans
to run, and a script outside the tolerance is sent back to add or cut a stated
number of words, at most `SCRIPT_LENGTH_REPAIRS` times. Still outside after
that, the film goes on but is flagged. The model here is a scripted stand-in:
every test says exactly what each call returns, so what is checked is the
agent's handling of it and not any real model.
"""
from __future__ import annotations

import pytest

from agents.orchestrator import PipelineOrchestrator
from agents.story_agent import StoryAgent, current_length_check
from agents.story_agent.agent import repair_prompt, script_planned_ms
from shared.constants import film_length_tolerance, script_length_repairs
from shared.schemas.pipeline import PipelineState
from shared.schemas.story import ScriptOutput
from shared.timeline import (
    EXPECTED_LINES_PER_SCENE, WORDS_PER_SECOND, estimate_line_ms, planned_length_ms,
    scene_overhead_ms, word_budget,
)

TARGET_S = 30
SCENES = 3


@pytest.fixture(autouse=True)
def _isolated(isolated_dirs):
    """A script run writes its artifacts under the outputs folder: keep them out of data/."""


def _script(words_per_line: int, lines_per_scene: int = 2, scenes: int = SCENES) -> ScriptOutput:
    """A valid script whose every line has `words_per_line` words."""
    from agents.story_agent.planner import template_script

    script = template_script("pid", "A lighthouse keeper befriends a stranded whale",
                             target_duration_s=TARGET_S, scene_count=scenes)
    speakers = [c.id for c in script.characters.characters]
    for scene in script.scenes:
        first = scene.dialogue[0]
        scene.dialogue = [
            first.model_copy(update={
                "line_id": f"{scene.scene_id}_l{j + 1}",
                "character_id": speakers[j % len(speakers)],
                "text": " ".join(["word"] * words_per_line),
            }) for j in range(lines_per_scene)]
    script.story.target_duration_s = TARGET_S
    return script


def _on_target() -> ScriptOutput:
    """Words per line chosen so the planned length is the target (checked below)."""
    total, _ = word_budget(TARGET_S, SCENES)
    return _script(round(total / (SCENES * EXPECTED_LINES_PER_SCENE)))


class FakeModel:
    """Answers each structured call with the next item: a script, or an exception."""

    provider = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def generate_structured(self, prompt, schema, **_kw):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer.model_copy(deep=True)


def _agent(*answers) -> tuple[StoryAgent, FakeModel]:
    agent = StoryAgent()
    model = FakeModel(*answers)
    agent.llm = model
    return agent, model


def _run(agent: StoryAgent, state: PipelineState | None = None) -> PipelineState:
    state = state or PipelineState(project_id="pid", user_prompt="A lighthouse keeper")
    agent.run(state, target_duration_s=TARGET_S, scene_count=SCENES)
    return state


def _off(script: ScriptOutput) -> float:
    return (script_planned_ms(script) - TARGET_S * 1000) / (TARGET_S * 1000)


# ---- the numbers the prompt and the check are built from -------------------------

def test_the_budget_prompt_and_the_estimate_agree_on_a_film_of_the_target_length():
    total, per_scene = word_budget(TARGET_S, SCENES)
    assert per_scene == round(total / SCENES)
    # A script that spends exactly the budget plans to within the tolerance of the
    # target (this is what stops the prompt and the timeline drifting apart).
    assert abs(_off(_on_target())) <= film_length_tolerance()


def test_planned_length_is_the_timelines_own_arithmetic():
    lines = [["one two three four five six", "seven eight"], ["nine ten eleven"]]
    expected = (sum(estimate_line_ms(t) for scene in lines for t in scene)
                + sum(scene_overhead_ms(len(scene)) for scene in lines))
    assert planned_length_ms(lines) == expected


def test_the_speaking_rate_is_the_measured_kokoro_rate():
    """DST-10 run R: 322 words in 122.5 s of Kokoro speech = 2.63 words/s."""
    assert WORDS_PER_SECOND == pytest.approx(322 / 122.5, abs=0.05)


def test_the_story_prompt_states_the_budget_the_check_uses():
    agent, model = _agent(_on_target())
    _run(agent)
    total, per_scene = word_budget(TARGET_S, SCENES)
    assert f"about {total} words" in model.prompts[0]
    assert f"about {per_scene} words per scene" in model.prompts[0]


# ---- the check and the repairs -----------------------------------------------------

def test_an_on_target_script_passes_with_no_repair():
    agent, model = _agent(_on_target())
    state = _run(agent)
    check = state.metadata["length_check"]
    assert len(model.prompts) == 1                      # only the script itself
    assert check["within"] is True and check["repairs"] == 0
    assert check["written_by"] == "model" and check["target_s"] == TARGET_S


def test_a_short_script_triggers_a_repair_that_names_the_words_to_add():
    short, fixed = _script(3), _on_target()
    assert _off(short) < -film_length_tolerance()
    agent, model = _agent(short, fixed)
    state = _run(agent)

    assert len(model.prompts) == 2
    repair = model.prompts[1]
    words = sum(len(ln.text.split()) for s in short.scenes for ln in s.dialogue)
    assert "too short" in repair and "Add about" in repair and f"It has {words} words" in repair
    assert "same characters" in repair and short.model_dump_json() in repair
    assert state.script.scenes[0].dialogue[0].text == fixed.scenes[0].dialogue[0].text
    check = state.metadata["length_check"]
    assert check["within"] is True and check["repairs"] == 1


def test_a_long_script_is_asked_to_cut():
    long_ = _script(14)
    assert _off(long_) > film_length_tolerance()
    agent, model = _agent(long_, _on_target())
    _run(agent)
    assert "too long" in model.prompts[1] and "Cut about" in model.prompts[1]


def test_a_script_that_stays_short_is_flagged_after_the_repair_limit():
    agent, model = _agent(_script(3), _script(4), _script(5))
    state = _run(agent)

    assert len(model.prompts) == 1 + script_length_repairs()   # never more than the limit
    check = state.metadata["length_check"]
    assert check["within"] is False and check["repairs"] == script_length_repairs()
    # The best script seen is the one kept: the last, closest to the target.
    assert state.script.scenes[0].dialogue[0].text == " ".join(["word"] * 5)


def test_a_repair_that_makes_it_worse_is_not_kept():
    agent, _ = _agent(_script(5), _script(2), _script(2))
    state = _run(agent)
    assert state.script.scenes[0].dialogue[0].text == " ".join(["word"] * 5)


def test_the_repair_limit_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("SCRIPT_LENGTH_REPAIRS", "1")
    agent, model = _agent(_script(3), _script(4), _script(5))
    _run(agent)
    assert len(model.prompts) == 2
    monkeypatch.setenv("SCRIPT_LENGTH_REPAIRS", "0")
    agent, model = _agent(_script(3))
    state = _run(agent)
    assert len(model.prompts) == 1 and state.metadata["length_check"]["repairs"] == 0
    monkeypatch.setenv("SCRIPT_LENGTH_REPAIRS", "many")           # back to the default
    assert script_length_repairs() == 2


def test_a_repair_that_changes_the_cast_is_discarded():
    short = _script(3)
    recast = _on_target()
    recast.characters.characters[0].id = "char_somebody_new"
    agent, model = _agent(short, recast, recast)
    state = _run(agent)
    assert state.script.scenes[0].dialogue[0].text == " ".join(["word"] * 3)   # the original stays
    assert state.metadata["length_check"]["within"] is False


def test_a_repair_that_changes_the_scenes_is_discarded():
    agent, _ = _agent(_script(3), _script(8, scenes=2), _script(8, scenes=2))
    state = _run(agent)
    assert len(state.script.scenes) == SCENES


def test_a_repair_where_the_provider_fails_keeps_the_script_and_does_not_retry():
    agent, model = _agent(_script(3), RuntimeError("no provider produced valid output"))
    state = _run(agent)
    assert len(model.prompts) == 2                     # the failed repair was the last call
    assert len(state.script.scenes) == SCENES          # the story was not thrown away
    assert state.metadata["length_check"]["written_by"] == "model"
    assert state.metadata["length_check"]["within"] is False


def test_the_target_is_what_was_asked_not_what_the_model_echoed():
    script = _on_target()
    script.story.target_duration_s = 999
    agent, _ = _agent(script)
    state = _run(agent)
    assert state.script.story.target_duration_s == TARGET_S


def test_the_template_is_checked_but_never_repaired(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    state = _run(StoryAgent())
    check = state.metadata["length_check"]
    assert check["written_by"] == "template" and check["repairs"] == 0
    assert check["within"] is True                      # the template budgets by the same estimate


# ---- flagging ---------------------------------------------------------------------

def test_a_script_still_off_target_is_flagged_in_the_completion_notes():
    agent, _ = _agent(_script(3), _script(3), _script(3))
    state = _run(agent)
    note, payload = PipelineOrchestrator._completion_notes(state)
    warning = payload["script_length_warning"]
    assert warning["target_s"] == TARGET_S and warning["repairs"] == 2
    assert warning["planned_s"] == round(script_planned_ms(state.script) / 1000, 1)
    assert f"the script plans {warning['planned_s']:.1f} s for a {TARGET_S} s target" in note


def test_a_script_within_the_tolerance_adds_no_flag():
    agent, _ = _agent(_on_target())
    note, payload = PipelineOrchestrator._completion_notes(_run(agent))
    assert note == "" and "script_length_warning" not in payload


def test_the_flag_follows_the_script_when_the_creator_edits_a_line():
    agent, _ = _agent(_script(3), _script(3), _script(3))
    state = _run(agent)
    assert current_length_check(state)["within"] is False
    for scene in state.script.scenes:                   # the creator writes it up to length
        for ln in scene.dialogue:
            ln.text = " ".join(["word"] * round(word_budget(TARGET_S, SCENES)[0] / 6))
    assert current_length_check(state)["within"] is True
    assert "script_length_warning" not in PipelineOrchestrator._completion_notes(state)[1]


def test_repair_prompt_goal_is_the_current_words_plus_the_change():
    script = _script(3)
    prompt = repair_prompt(script, script_planned_ms(script), TARGET_S)
    words = 3 * 2 * SCENES
    change = int(prompt.split("Add about ")[1].split(" words")[0])
    assert f"about {words + change} words in all" in prompt


def test_the_storyboard_answer_carries_the_check(isolated_dirs, monkeypatch):
    """The endpoint recomputes it, so a reload shows what the plan found."""
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from state_manager.state_manager import StateManager
    from state_manager.storage import VersionStore
    from tests.conftest import signed_in_client

    sm = StateManager(VersionStore(isolated_dirs / "state.db"))
    orch = PipelineOrchestrator(state_manager=sm)
    orch.story.llm = FakeModel(_script(3), _script(3), _script(3))
    state = orch.plan("A lighthouse keeper", target_duration_s=TARGET_S, scene_count=SCENES,
                      preview_width=320, preview_height=180)
    monkeypatch.setattr(pipeline_routes, "sm", sm)
    client = signed_in_client(app_module.app)

    length = client.get(f"/api/pipeline/storyboard/{state.project_id}").json()["length"]
    assert length["within"] is False and length["error"] < 0
    assert length["target_s"] == TARGET_S and length["repairs"] == 2
