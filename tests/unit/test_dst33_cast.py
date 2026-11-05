"""DST-33: only characters who speak are in the film.

A character appears on screen only through their lines, so a listed character with
none is drawn and voiced for nothing. The story prompt now sizes the cast to the
film and asks every character to speak; a character the creator named in their own
prompt who is still silent gets one repair; anyone still silent is trimmed from the
cast before voices, portraits and every file for later phases are made.

The model is a scripted stand-in (as in test_script_length.py): each test says what
every call returns, so what is checked is the agent's handling and not a real model.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.edit_agent.executor import EditExecutor
from agents.edit_agent.planner import EditStep
from agents.orchestrator import PipelineOrchestrator
from agents.orchestrator.storyboard_view import cast, cast_note, image_budget
from agents.story_agent import StoryAgent
from agents.story_agent import cast as casting
from agents.story_agent.agent import (
    CAST_REPAIR_PROMPT, SCRIPT_USER_PROMPT, _changed_shape, cast_repair_prompt,
    script_planned_ms,
)
from agents.story_agent.planner import template_script
from shared import constants
from shared.constants import script_cast_repairs
from shared.schemas.pipeline import PipelineState
from shared.schemas.story import Character, ScriptOutput
from shared.timeline import (
    LINES_PER_CHARACTER, MAX_CAST, MIN_CAST, WORDS_PER_LINE, cast_limit, line_room, word_budget,
)
from state_manager.state_manager import StateManager
from state_manager.storage import VersionStore
from tests.conftest import signed_in_client

TARGET_S, SCENES = 30, 3
PROMPT = "A lighthouse keeper befriends a stranded whale"
NAMED = "Aria and Kai search the lighthouse"           # names two of the template's cast
NARR, ARIA, KAI, EXTRA = "char_narrator", "char_protagonist", "char_supporting", "char_extra"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated(isolated_dirs):
    """Script runs write their artifacts under the outputs folder: keep them out of data/."""


def _character(cid: str, name: str, role: str = "supporting") -> Character:
    return Character(id=cid, name=name, role=role, description="d", visual_description="v")


def _film(speaking, extra=(), words=None) -> ScriptOutput:
    """A valid 3-scene script on the template's cast (Narrator, Aria, Kai) in which
    `speaking[i]` lists, in order, who speaks each scene's lines. Words per line are
    chosen so the planned length stays on target, whatever the number of lines."""
    script = template_script("pid", PROMPT, target_duration_s=TARGET_S, scene_count=SCENES)
    script.characters.characters += [_character(cid, name) for cid, name in extra]
    total_lines = sum(len(who) for who in speaking)
    per_line = words or max(3, round(word_budget(TARGET_S, SCENES)[0] / max(1, total_lines)))
    for scene, who in zip(script.scenes, speaking):
        first = scene.dialogue[0]
        scene.dialogue = [
            first.model_copy(update={"line_id": f"{scene.scene_id}_l{j + 1}", "character_id": cid,
                                     "text": " ".join(["word"] * per_line)})
            for j, cid in enumerate(who)]
    script.story.target_duration_s = TARGET_S
    return script


def _off(script: ScriptOutput) -> float:
    return abs(script_planned_ms(script) - TARGET_S * 1000) / (TARGET_S * 1000)


KAI_SILENT = [[NARR, ARIA], [ARIA, NARR], [NARR, ARIA]]
KAI_SPEAKS = [[NARR, ARIA], [ARIA, KAI], [NARR, ARIA]]


class FakeModel:
    provider = "fake"

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def generate_structured(self, prompt, schema, **_kw):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer.model_copy(deep=True)


def _agent(*answers):
    agent = StoryAgent()
    agent.llm = FakeModel(*answers)
    return agent, agent.llm


def _run(agent, prompt=PROMPT) -> PipelineState:
    state = PipelineState(project_id="pid", user_prompt=prompt)
    agent.run(state, target_duration_s=TARGET_S, scene_count=SCENES)
    return state


def _names(state) -> list:
    return [c.name for c in state.script.characters.characters]


# ---- the cast limit: sized to the film, from measured numbers ---------------------------

@pytest.mark.parametrize("target_s, scenes, limit", [
    (15, 2, 2), (20, 2, 3), (30, 3, 4), (45, 4, 4), (60, 5, 4), (60, 6, 4), (120, 2, 4),
])
def test_the_cast_limit_follows_the_length_of_the_film(target_s, scenes, limit):
    assert cast_limit(target_s, scenes) == limit


def test_the_limit_never_shrinks_as_the_film_grows_and_stays_in_bounds():
    for scenes in range(1, 9):
        limits = [cast_limit(t, scenes) for t in range(15, 301, 5)]
        assert limits == sorted(limits)
        assert all(MIN_CAST <= n <= MAX_CAST for n in limits)


def test_the_shortest_film_gets_a_narrator_and_one_voice_not_four_characters():
    assert cast_limit(15, 2) == MIN_CAST == 2


def test_the_prompt_asks_for_a_range_so_models_do_not_shrink_the_cast_to_one():
    """The first live run, told only to list what the story needs, made 6 of 25 films a
    monologue (1 of 24 before): so the prompt names a range and the floor is 2."""
    from agents.story_agent.agent import _cast_size_text
    from shared.timeline import cast_range
    assert cast_range(30, 3) == (2, 4) and cast_range(20, 2) == (2, 3) and cast_range(15, 2) == (2, 2)
    assert [_cast_size_text(*a) for a in ((30, 3), (20, 2), (15, 2))] == ["2 to 4", "2 to 3", "2"]


def test_lines_a_film_has_room_for_track_the_lines_models_actually_wrote():
    """Measured on the 46 model-written scripts of runs G and L (2026-11-04):
    (target s, scenes) -> average lines. The estimate may read a little high (models
    write about a quarter fewer words than budgeted) but must stay close."""
    measured = {(20, 2): 3.9, (30, 3): 6.8, (45, 4): 9.0, (60, 5): 11.5}
    for (target_s, scenes), lines in measured.items():
        assert 0.9 <= line_room(target_s, scenes) / lines <= 1.4, (target_s, scenes)


def test_words_per_line_is_the_measured_value():
    assert WORDS_PER_LINE == pytest.approx(8.1, abs=0.3)
    assert LINES_PER_CHARACTER == 1.5


# ---- which characters the creator named -------------------------------------------------

def _cast(*pairs):
    return [_character(f"c{i}", name, role) for i, (name, role) in enumerate(pairs)]


def test_a_character_is_named_when_a_distinctive_part_of_the_name_is_in_the_prompt():
    people = _cast(("Batman", "protagonist"), ("The Joker", "antagonist"), ("Alfred", "supporting"))
    named = casting.named_in_prompt(people, "batman and the joker fight in Gotham")
    assert named == {"c0", "c1"}


def test_matching_ignores_case_possessives_and_punctuation():
    people = _cast(("Aria", "protagonist"), ("Kai", "supporting"))
    assert casting.named_in_prompt(people, "ARIA's lantern; kai, quietly.") == {"c0", "c1"}


def test_the_narrator_is_never_named_by_the_creator():
    people = _cast(("Narrator", "narrator"), ("Aria", "protagonist"))
    assert casting.named_in_prompt(people, "the narrator tells how Aria sails") == {"c1"}


def test_a_narrator_with_a_distinctive_name_is_still_not_named_by_the_creator():
    # The role decides, not the word: "Zora" in the prompt asks for a character called
    # Zora, not for the film's narrator, who the pipeline adds on its own.
    people = _cast(("Zora", "narrator"), ("Aria", "protagonist"))
    assert casting.named_in_prompt(people, "Zora's tale of Aria") == {"c1"}


def test_generic_words_in_a_name_do_not_count_as_named():
    # "an old man" does not name a character called Old Man; a false match would
    # cost the creator a note about someone they never asked for.
    people = _cast(("Old Man", "supporting"), ("Corporal Thomas", "supporting"))
    assert casting.named_in_prompt(people, "an old man waits for Thomas") == {"c1"}


def test_whole_words_only_and_short_parts_ignored():
    people = _cast(("Aria", "protagonist"), ("Jo", "supporting"))
    assert casting.named_in_prompt(people, "arial fonts and jo") == set()


def test_silent_characters_come_in_roster_order_and_named_ones_are_picked_out():
    script = _film(KAI_SILENT, extra=[(EXTRA, "Zephyr")])
    assert [c.id for c in casting.silent_characters(script)] == [KAI, EXTRA]
    assert [c.id for c in casting.silent_but_named(script, "Kai in the storm")] == [KAI]


# ---- the trim ------------------------------------------------------------------------------

def test_a_character_with_no_lines_is_removed_and_everyone_else_stays_in_order():
    script = _film(KAI_SILENT, extra=[(EXTRA, "Zephyr")])
    removed = casting.trim_silent_cast(script, PROMPT)
    assert [c.id for c in script.characters.characters] == [NARR, ARIA]
    assert [(r["id"], r["named_in_prompt"]) for r in removed] == [(KAI, False), (EXTRA, False)]


def test_a_silent_narrator_goes_too_whatever_the_role():
    script = _film([[ARIA, KAI], [KAI, ARIA], [ARIA, KAI]])
    removed = casting.trim_silent_cast(script, PROMPT)
    assert [c.id for c in script.characters.characters] == [ARIA, KAI]
    assert removed[0]["role"] == "narrator"


def test_lines_speakers_and_scenes_are_not_touched():
    script = _film(KAI_SILENT, extra=[(EXTRA, "Zephyr")])
    before = [s.model_dump() for s in script.scenes]
    casting.trim_silent_cast(script, PROMPT)
    assert [s.model_dump() for s in script.scenes] == before


def test_the_cast_is_never_emptied_when_nobody_speaks():
    script = _film([[], [], []])
    before = [c.id for c in script.characters.characters]
    assert casting.trim_silent_cast(script, PROMPT) == []
    assert [c.id for c in script.characters.characters] == before


def test_a_script_where_everyone_speaks_is_left_alone_and_a_second_trim_removes_nothing():
    script = _film(KAI_SPEAKS)
    assert casting.trim_silent_cast(script, PROMPT) == []
    silent = _film(KAI_SILENT)
    casting.trim_silent_cast(silent, PROMPT)
    assert casting.trim_silent_cast(silent, PROMPT) == []


def test_a_one_member_cast_when_the_narrator_speaks_every_line():
    script = _film([[NARR, NARR], [NARR], [NARR, NARR]])
    casting.trim_silent_cast(script, PROMPT)
    assert [c.id for c in script.characters.characters] == [NARR]


def test_names_are_joined_the_way_a_person_says_them():
    assert [casting.join_names(n) for n in ([], ["Kai"], ["Kai", "Aria"], ["Kai", "Aria", "Dog"])] == [
        "", "Kai", "Kai and Aria", "Kai, Aria and Dog"]


def test_each_record_says_whether_the_creator_named_that_character():
    script = _film(KAI_SILENT, extra=[(EXTRA, "Zephyr")])
    removed = casting.trim_silent_cast(script, NAMED)
    assert {r["name"]: r["named_in_prompt"] for r in removed} == {"Kai": True, "Zephyr": False}
    assert casting.removed_names_for_creator(removed) == ["Kai"]


# ---- the story agent: the trim comes before anything reads the cast -----------------

def test_the_trimmed_cast_is_what_the_state_and_every_phase_file_hold():
    agent, model = _agent(_film(KAI_SILENT, extra=[(EXTRA, "Zephyr")]))
    state = _run(agent)
    assert len(model.prompts) == 1                       # an unnamed extra is not worth a call
    assert _names(state) == ["Narrator", "Aria"]

    out = Path(state.phase1.artifact_paths[0]).parent
    on_disk = [c["id"] for c in json.loads((out / "characters.json").read_text())["characters"]]
    assert on_disk == [NARR, ARIA]
    handoff = json.loads((out / "phase2_audio_handoff.json").read_text())
    assert [v["character_id"] for v in handoff["voice_configs"]] == [NARR, ARIA]
    assert json.loads((out / "summary.json").read_text())["character_count"] == 2


def test_the_removed_names_are_logged_and_kept_in_the_projects_metadata(caplog):
    agent, _ = _agent(_film(KAI_SILENT, extra=[(EXTRA, "Zephyr")]))
    with caplog.at_level(logging.INFO, logger="story_agent"):
        state = _run(agent)
    check = state.metadata["cast_check"]
    assert [(r["name"], r["role"], r["named_in_prompt"]) for r in check["removed"]] == [
        ("Kai", "supporting", False), ("Zephyr", "supporting", False)]
    assert (check["limit"], check["listed"], check["kept"], check["repairs"]) == (4, 4, 2, 0)
    assert "Kai (supporting), Zephyr (supporting)" in caplog.text


def test_a_creator_is_told_nothing_when_the_writer_invented_the_extra():
    agent, _ = _agent(_film(KAI_SILENT, extra=[(EXTRA, "Zephyr")]))
    assert cast_note(_run(agent)) is None


def test_a_script_where_everyone_speaks_has_nothing_removed():
    agent, _ = _agent(_film(KAI_SPEAKS))
    state = _run(agent)
    assert _names(state) == ["Narrator", "Aria", "Kai"]
    assert state.metadata["cast_check"]["removed"] == []


def test_a_second_run_replaces_the_record_instead_of_keeping_a_stale_one():
    # What an edit that rewrites the script does: it runs the story agent again.
    state = _run(_agent(_film(KAI_SILENT))[0])
    assert state.metadata["cast_check"]["removed"]
    second, _ = _agent(_film(KAI_SPEAKS))
    second.run(state, target_duration_s=TARGET_S, scene_count=SCENES)
    assert state.metadata["cast_check"]["removed"] == [] and len(state.script.characters.characters) == 3


# ---- the template: the same rule, and a synopsis that tells the truth ------------------

def _testset():
    return [json.loads(line) for line in (ROOT / "eval" / "testset.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]


def _template_run(case) -> PipelineState:
    state = PipelineState(project_id="pid", user_prompt=case["prompt"])
    StoryAgent().run(state, target_duration_s=case["target_duration_s"],
                     scene_count=case["expected_scenes"])
    return state


def test_every_remaining_template_character_speaks_across_the_whole_test_set():
    for case in _testset():
        state = _template_run(case)
        spoken = casting.speakers(state.script)
        assert {c.id for c in state.script.characters.characters} <= spoken, case["id"]
        assert state.metadata["cast_check"]["kept"] >= 1, case["id"]


def test_the_template_leaves_kai_out_of_the_short_films_where_he_never_speaks():
    silent = {}
    for case in _testset():
        script = template_script("p", case["prompt"], target_duration_s=case["target_duration_s"],
                                 scene_count=case["expected_scenes"])
        silent[case["id"]] = [c.name for c in casting.silent_characters(script)]
        state = _template_run(case)
        assert ("Kai" in _names(state)) == ("Kai" not in silent[case["id"]]), case["id"]
    assert {k for k, v in silent.items() if v} == {"dst-03", "dst-07", "dst-15", "dst-24"}


def test_the_template_synopsis_names_only_people_who_speak():
    for case in _testset():
        state = _template_run(case)
        synopsis = state.script.story.synopsis
        assert ("Kai" in synopsis) == ("Kai" in _names(state)), case["id"]
        assert ("Aria" in synopsis) == ("Aria" in _names(state)), case["id"]


def test_the_template_script_itself_still_lists_its_whole_cast():
    # The trim is the story agent's job; the template is a plain generator.
    script = template_script("p", "Rain", target_duration_s=20, scene_count=2)
    assert [c.name for c in script.characters.characters] == ["Narrator", "Aria", "Kai"]


# ---- the prompts ------------------------------------------------------------------------------

def test_the_story_prompt_sizes_the_cast_and_asks_every_character_to_speak():
    agent, model = _agent(_film(KAI_SPEAKS))
    _run(agent)
    prompt = " ".join(model.prompts[0].split())           # the template wraps its lines
    assert "3-4 characters total" not in prompt
    assert f"choose 2 to {cast_limit(TARGET_S, SCENES)} characters for a {TARGET_S}-second" in prompt
    assert "Use fewer only when the story is deliberately about a single voice" in prompt
    assert f"room for about {line_room(TARGET_S, SCENES)} lines" in prompt
    assert "MUST speak at least one line" in prompt
    assert "Do not list anyone who does not speak" in prompt
    assert "names MUST be in the roster and MUST speak" in prompt


def test_the_story_prompt_template_carries_the_cast_placeholders():
    for name in ("max_cast", "line_room", "duration_s", "scene_count"):
        assert "{" + name + "}" in SCRIPT_USER_PROMPT
    assert "{names}" in CAST_REPAIR_PROMPT and "{prompt}" in CAST_REPAIR_PROMPT


def test_the_cast_repair_names_the_characters_and_quotes_the_creators_prompt():
    script = _film(KAI_SILENT, extra=[(EXTRA, "Zephyr")])
    kai = [c for c in script.characters.characters if c.id == KAI]
    text = cast_repair_prompt(script, NAMED, kai)
    assert 'The user\'s prompt was: "Aria and Kai search the lighthouse"' in text
    assert "It names Kai, but your script gives that character no lines" in text
    assert "same characters" in text and script.model_dump_json() in text
    both = cast_repair_prompt(script, NAMED, script.characters.characters[1:3])
    assert "Aria and Kai" in both and "each of them" in both


def test_the_length_repair_asks_everyone_who_speaks_to_keep_a_line():
    from agents.story_agent.agent import LENGTH_REPAIR_PROMPT
    assert "everyone who speaks now keeps at least one line" in LENGTH_REPAIR_PROMPT


# ---- the repair for characters the creator named ---------------------------------------

def test_a_named_silent_character_gets_one_repair_that_gives_them_lines():
    agent, model = _agent(_film(KAI_SILENT), _film(KAI_SPEAKS))
    state = _run(agent, NAMED)
    assert len(model.prompts) == 2
    assert "It names Kai" in model.prompts[1]
    assert _names(state) == ["Narrator", "Aria", "Kai"]
    check = state.metadata["cast_check"]
    assert check["repairs"] == 1 and check["removed"] == []
    assert cast_note(state) is None


@pytest.mark.parametrize("why, candidate, reason", [
    ("the cast changed", lambda: _film(KAI_SPEAKS, extra=[(EXTRA, "Zephyr")]), "the cast changed"),
    ("the scenes changed", lambda: _renamed_scene(_film(KAI_SPEAKS)), "the scenes changed"),
    ("nobody new speaks", lambda: _film(KAI_SILENT), "no fewer named characters have lines"),
    ("it silences someone who spoke", lambda: _film([[NARR, KAI], [KAI, NARR], [NARR, KAI]]),
     "a character who spoke lost all their lines"),
    ("it pushes the length out", lambda: _film(KAI_SPEAKS, words=1),
     "it pushed the length out of tolerance"),
])
def test_a_repair_that_does_not_help_is_discarded_and_the_creator_is_told(why, candidate, reason, caplog):
    assert _off(_film(KAI_SILENT)) <= constants.film_length_tolerance()      # the premise
    agent, model = _agent(_film(KAI_SILENT), candidate())
    with caplog.at_level(logging.WARNING, logger="story_agent"):
        state = _run(agent, NAMED)
    assert len(model.prompts) == 2, why
    assert reason in caplog.text, why
    # The original script is kept, so Kai (who the creator named) is trimmed, and said so.
    assert "Kai" not in _names(state) and len(state.script.scenes) == SCENES
    assert state.metadata["cast_check"]["removed"][0]["named_in_prompt"] is True
    assert cast_note(state) == "Kai was named in your prompt but has no lines in the script, so isn't in the cast."


def _renamed_scene(script: ScriptOutput) -> ScriptOutput:
    script.scenes[0].scene_id = "scene_renamed"
    return script


def test_a_provider_that_fails_ends_the_repair_and_keeps_the_script(caplog):
    agent, model = _agent(_film(KAI_SILENT), RuntimeError("rate limited"))
    with caplog.at_level(logging.WARNING, logger="story_agent"):
        state = _run(agent, NAMED)
    assert len(model.prompts) == 2 and "cast repair 1 failed" in caplog.text
    assert "Kai" not in _names(state) and cast_note(state)


def test_the_repair_can_be_turned_off_and_the_trim_still_runs(monkeypatch):
    monkeypatch.setenv("SCRIPT_CAST_REPAIRS", "0")
    agent, model = _agent(_film(KAI_SILENT))
    state = _run(agent, NAMED)
    assert len(model.prompts) == 1 and "Kai" not in _names(state)


def test_the_number_of_cast_repairs_is_read_from_the_environment(monkeypatch, caplog):
    monkeypatch.delenv("SCRIPT_CAST_REPAIRS", raising=False)
    assert script_cast_repairs() == 1
    monkeypatch.setenv("SCRIPT_CAST_REPAIRS", "2")
    assert script_cast_repairs() == 2
    for bad in ("many", "-1", "9"):
        monkeypatch.setenv("SCRIPT_CAST_REPAIRS", bad)
        with caplog.at_level(logging.WARNING, logger="constants"):
            assert script_cast_repairs() == 1
    assert caplog.text.count("SCRIPT_CAST_REPAIRS") == 3


def test_a_failed_repair_does_not_stop_a_second_chance_when_two_are_allowed(monkeypatch):
    monkeypatch.setenv("SCRIPT_CAST_REPAIRS", "2")
    agent, model = _agent(_film(KAI_SILENT), _film(KAI_SILENT), _film(KAI_SPEAKS))
    state = _run(agent, NAMED)
    assert len(model.prompts) == 3 and "Kai" in _names(state)
    assert state.metadata["cast_check"]["repairs"] == 2


def test_a_repair_that_leaves_the_length_inside_the_tolerance_is_accepted():
    # Not a free pass for length: the repair is judged against the tolerance, not perfection.
    candidate = _film(KAI_SPEAKS)
    assert _off(candidate) <= constants.film_length_tolerance()
    agent, _ = _agent(_film(KAI_SILENT), candidate)
    assert "Kai" in _names(_run(agent, NAMED))


# ---- a length repair cannot silence anyone -------------------------------------------------

def test_a_length_repair_that_silences_a_speaker_is_discarded(caplog):
    short = _film(KAI_SPEAKS, words=3)                       # outside the tolerance: gets repaired
    assert _off(short) > constants.film_length_tolerance()
    fixed_but_silences_aria = _film([[NARR, KAI], [KAI, NARR], [NARR, KAI]])
    agent, model = _agent(short, fixed_but_silences_aria, fixed_but_silences_aria)
    with caplog.at_level(logging.WARNING, logger="story_agent"):
        state = _run(agent)
    assert "a character who spoke lost all their lines" in caplog.text
    assert _names(state) == ["Narrator", "Aria", "Kai"]       # nobody lost
    assert state.metadata["length_check"]["within"] is False


def test_the_shape_check_reports_a_speaker_silenced():
    before, after = _film(KAI_SPEAKS), _film([[NARR, KAI], [KAI, NARR], [NARR, KAI]])
    assert _changed_shape(before, after) == "a character who spoke lost all their lines"
    assert _changed_shape(before, _film(KAI_SPEAKS)) is None


# ---- the creator is told: the plan, the page, the cast row's data ----------------------------

@pytest.fixture
def orch(isolated_dirs):
    return PipelineOrchestrator(state_manager=StateManager(VersionStore(isolated_dirs / "state.db")))


def _plan(orch, agent, prompt, **kw):
    orch.story = agent
    return orch.plan(prompt, target_duration_s=TARGET_S, scene_count=SCENES,
                     preview_width=320, preview_height=180, **kw)


def test_a_named_character_who_had_to_go_is_in_the_plans_events_and_message(orch):
    events = []
    agent, _ = _agent(_film(KAI_SILENT), _film(KAI_SILENT))
    state = _plan(orch, agent, NAMED, on_event=events.append)

    script = next(e for e in events if (e.phase, e.status) == ("storyboard", "script"))
    said = "Kai was named in your prompt but has no lines in the script, so isn't in the cast."
    assert script.payload["cast_note"] == said
    assert [m["name"] for m in script.payload["cast"]] == ["Narrator", "Aria"]
    done = next(e for e in events if (e.phase, e.status) == ("storyboard", "complete"))
    assert said in done.message and done.payload["cast_removed"] == ["Kai"]
    assert "Kai" not in _names(state)


def test_an_invented_extra_who_had_to_go_adds_nothing_to_the_plans_message(orch):
    events = []
    agent, _ = _agent(_film(KAI_SILENT, extra=[(EXTRA, "Zephyr")]))
    _plan(orch, agent, PROMPT, on_event=events.append)
    done = next(e for e in events if (e.phase, e.status) == ("storyboard", "complete"))
    assert "cast_removed" not in done.payload and "named in your prompt" not in done.message
    script = next(e for e in events if (e.phase, e.status) == ("storyboard", "script"))
    assert script.payload["cast_note"] is None


def test_the_storyboard_answer_carries_the_note_so_a_reload_says_what_the_stream_said(orch, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes

    agent, _ = _agent(_film(KAI_SILENT), _film(KAI_SILENT))
    state = _plan(orch, agent, NAMED)
    monkeypatch.setattr(pipeline_routes, "sm", orch.sm)
    body = signed_in_client(app_module.app).get(f"/api/pipeline/storyboard/{state.project_id}").json()
    assert body["cast_note"] == cast_note(state) and "Kai" in body["cast_note"]
    assert [m["name"] for m in body["cast"]] == ["Narrator", "Aria"]


def test_two_named_characters_are_listed_in_one_sentence():
    state = SimpleNamespace(metadata={"cast_check": {"removed": [
        {"name": "Kai", "named_in_prompt": True}, {"name": "Aria", "named_in_prompt": True},
        {"name": "Zephyr", "named_in_prompt": False}]}})
    assert cast_note(state) == ("Kai and Aria were named in your prompt but have no lines "
                                "in the script, so aren't in the cast.")
    three = SimpleNamespace(metadata={"cast_check": {"removed": [
        {"name": n, "named_in_prompt": True} for n in ("A", "B", "C")]}})
    assert cast_note(three).startswith("A, B and C were named")
    assert cast_note(None) is None and cast_note(SimpleNamespace(metadata={})) is None


# ---- portraits and the image budget follow the cast -------------------------------------------

def test_the_plan_draws_one_portrait_per_speaking_character_and_the_budget_says_so(orch):
    events = []
    agent, _ = _agent(_film(KAI_SILENT, extra=[(EXTRA, "Zephyr")]))
    state = _plan(orch, agent, PROMPT, on_event=events.append)
    people = state.script.characters.characters

    assert {p.character_id for p in state.storyboard.portraits} == {c.id for c in people} == {NARR, ARIA}
    drawn = [e for e in events if (e.phase, e.status) == ("storyboard", "portrait")]
    assert {e.payload["character_id"] for e in drawn} == {NARR, ARIA}
    assert image_budget(state.script) == {"plan": SCENES + 2, "render": 0}   # 10 s scenes: no cutaway (DST-36)
    script = next(e for e in events if (e.phase, e.status) == ("storyboard", "script"))
    assert script.payload["images"]["plan"] == SCENES + 2


def test_a_film_the_narrator_carries_alone_shows_a_one_member_cast(orch):
    agent, _ = _agent(_film([[NARR, NARR], [NARR, NARR], [NARR, NARR]]))
    state = _plan(orch, agent, PROMPT)
    assert [m["name"] for m in cast(state.script, state=state)] == ["Narrator"]
    assert len(state.storyboard.portraits) == 1
    assert image_budget(state.script)["plan"] == SCENES + 1


# ---- the rewrite-script edit goes through the same agent -------------------------------------

def test_an_edit_that_rewrites_the_script_trims_the_cast_too(orch, monkeypatch):
    import agents.story_agent as story_package

    state = _plan(orch, _agent(_film(KAI_SPEAKS))[0], PROMPT)
    assert "Kai" in _names(state)

    rewrite, _ = _agent(_film(KAI_SILENT, extra=[(EXTRA, "Zephyr")]))
    monkeypatch.setattr(story_package, "StoryAgent", lambda: rewrite)   # what the executor builds
    EditExecutor()._step_regenerate_script(
        state, EditStep(name="regenerate_script", target="script", scope="global"))
    assert _names(state) == ["Narrator", "Aria"]
    assert [r["name"] for r in state.metadata["cast_check"]["removed"]] == ["Kai", "Zephyr"]
    out = Path(state.phase1.artifact_paths[0]).parent
    assert [c["id"] for c in json.loads((out / "characters.json").read_text())["characters"]] == [NARR, ARIA]


# ---- the production film that started this ----------------------------------------------------

PEACH_BOY = ("A boy born from a giant peach grows up to fight a band of ogres (oni) with the "
             "help of a talking dog, monkey, and pheasant.")


def _peach_boy_film(speaking):
    """The production film's shape: its cast (names as a writing model would give them)
    on the template's three, with `speaking` saying who speaks each of the three scenes."""
    cast_ = [("char_momo", "Momotaro"), ("char_dog", "Dog"), ("char_monkey", "Monkey"),
             ("char_pheasant", "Pheasant"), ("char_oni", "Oni Chief")]
    return _film(speaking, extra=cast_)


def test_the_production_film_loses_everyone_the_narrator_never_let_speak():
    """The film that started DST-33 listed six characters and let only the narrator
    speak. Whatever the model, the trim leaves a one-member cast and one portrait."""
    script = _peach_boy_film([[NARR, NARR], [NARR, NARR], [NARR, NARR]])
    removed = casting.trim_silent_cast(script, PEACH_BOY)
    assert [c.id for c in script.characters.characters] == [NARR]
    assert len(removed) == 7                                          # the template trio and the five


def test_the_production_prompt_names_the_animals_and_the_oni_but_not_the_boy_or_the_narrator():
    people = _peach_boy_film([[NARR]]).characters.characters
    named = {c.name for c in people if c.id in casting.named_in_prompt(people, PEACH_BOY)}
    # "Dog", "Monkey" and "Pheasant" are the creator's words; "oni" names the Oni Chief.
    # "Momotaro", Aria and Kai are not in the prompt, and "boy" is a generic word.
    assert named == {"Dog", "Monkey", "Pheasant", "Oni Chief"}


def test_a_character_the_writer_gave_a_proper_name_is_still_found_through_their_id():
    """Live, 2026-11-05: the production prompt came back with Inu, Saru and Kiji, ids
    char_dog, char_monkey and char_pheasant. The prompt said "dog, monkey, and pheasant":
    matching only on names would have called all three invented extras."""
    people = [_character("char_momotaro", "Momotaro", "protagonist"),
              _character("char_dog", "Inu"), _character("char_monkey", "Saru"),
              _character("char_pheasant", "Kiji"), _character("char_old_man", "Ojiisan"),
              _character("char_peach_boy", "Boy")]
    named = {c.name for c in people if c.id in casting.named_in_prompt(people, PEACH_BOY)}
    # "peach" is in the prompt and in the last id; the Old Man ("old", "man") and a bare
    # "Boy" are generic words, so "Ojiisan" is not named.
    assert named == {"Inu", "Saru", "Kiji", "Boy"}


def test_role_words_in_an_id_do_not_name_anyone():
    people = [_character("char_protagonist", "Mara", "protagonist"),
              _character("char_supporting", "Kade", "supporting")]
    assert casting.named_in_prompt(people, "a protagonist and a supporting cast hero") == set()


def test_the_production_prompt_gets_a_repair_for_the_named_animals_then_a_note_if_it_fails():
    silent = _peach_boy_film([[NARR, NARR], [NARR, "char_momo"], [NARR, NARR]])
    agent, model = _agent(silent, silent)                             # the repair changes nothing
    state = PipelineState(project_id="pid", user_prompt=PEACH_BOY)
    agent.run(state, target_duration_s=TARGET_S, scene_count=SCENES)

    assert len(model.prompts) == 2
    assert "It names Dog, Monkey, Pheasant and Oni Chief, but your script gives them no lines" in model.prompts[1]
    assert PEACH_BOY in model.prompts[1]
    assert _names(state) == ["Narrator", "Momotaro"]
    note = cast_note(state)
    assert note.startswith("Dog, Monkey, Pheasant and Oni Chief were named in your prompt")


def test_the_production_prompt_when_the_repair_works_keeps_the_animals_and_says_nothing():
    silent = _peach_boy_film([[NARR, NARR], [NARR, "char_momo"], [NARR, NARR]])
    fixed = _peach_boy_film([[NARR, "char_dog"], ["char_monkey", "char_momo"],
                             ["char_pheasant", "char_oni"]])
    agent, _ = _agent(silent, fixed)
    state = PipelineState(project_id="pid", user_prompt=PEACH_BOY)
    agent.run(state, target_duration_s=TARGET_S, scene_count=SCENES)
    assert _names(state) == ["Narrator", "Momotaro", "Dog", "Monkey", "Pheasant", "Oni Chief"]
    assert cast_note(state) is None and state.metadata["cast_check"]["repairs"] == 1
