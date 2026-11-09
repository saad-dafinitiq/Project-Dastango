"""DST-43 (L4): typed sentences the offline reader missed, and a sentence of several requests."""
from __future__ import annotations

import pytest

from agents.edit_agent import EditAgent
from agents.edit_agent.intent_classifier import IntentClassifier, numerals, split_request
from shared.schemas.edit import EditCommand
from tests.conftest import silence_tts

SCENES = ["scene_1", "scene_2", "scene_3"]


def _read(query: str):
    return IntentClassifier().classify(query, SCENES, [])


@pytest.mark.parametrize("query", ["turn the music off", "music off", "turn off the music", "switch the music off",
                                   "kill the music", "no more music"])
def test_every_way_of_switching_the_music_off_is_understood(query):
    got = _read(query)
    assert got.intent == "set_music" and got.parameters == {"off": True}, got


def test_music_off_in_a_scene_is_that_scene_only():
    got = _read("switch the music off in scene 2")
    assert (got.intent, got.scope, got.parameters) == ("set_music", "scene:scene_2", {"off": True})


def test_the_example_in_the_docs_works():
    got = _read("play something tense in scene 3")
    assert (got.intent, got.scope, got.parameters) == ("set_music", "scene:scene_3", {"mood": "tense"})


def test_a_scene_that_should_feel_a_mood_gets_that_music():
    got = _read("scene two should feel more tense")
    assert (got.intent, got.scope, got.parameters) == ("set_music", "scene:scene_2", {"mood": "tense"})


@pytest.mark.parametrize("query, effect", [("make it look like old film", "grain"), ("give it a vintage film look", "grain"),
                                           ("a 16mm look", "grain"), ("add film grain", "grain")])
def test_an_old_film_look_is_film_grain(query, effect):
    got = _read(query)
    assert got.intent == "set_effect" and got.parameters["effect"] == effect


def test_turning_the_music_down_changes_the_level_not_the_music():
    got = _read("turn the music down")
    assert got.intent == "set_volume" and got.parameters["percent"] < 100
    assert _read("turn the music up").parameters["percent"] > 100


def test_a_number_in_words_is_a_number_only_where_a_number_belongs():
    assert numerals("scene two should feel tense") == "scene 2 should feel tense"
    assert numerals("a fade between scenes 1 and two") == "a fade between scenes 1 and 2"
    assert numerals("the one in scene three") == "the one in scene 3"


@pytest.mark.parametrize("query, parts", [
    ("add rain and make the music quieter", ["add rain", "make the music quieter"]),
    ("add rain, make the music quieter and add film grain", ["add rain", "make the music quieter", "add film grain"]),
    ("add rain then turn the music off", ["add rain", "turn the music off"]),
    # one request: the "and" joins two scene numbers, two nouns, or sits inside quotes
    ("use a slow fade between scenes 1 and 2", None),
    ("make the music and the rain quieter", None),
    ('change line 2 of scene 1 to "Tom and make it quick"', None),
])
def test_a_sentence_is_split_only_where_a_second_request_begins(query, parts):
    assert split_request(query) == (parts or [query])


# ---- on a real film -----------------------------------------------------------------------------

def _agent(small_project, pid):
    state, sm = small_project(project_id=pid)
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    return state, sm, agent


def test_a_sentence_of_two_requests_applies_both_as_one_version(small_project):
    state, sm, agent = _agent(small_project, "t_s_two")
    done = agent.edit(EditCommand(project_id="t_s_two", query="add rain and turn the music off"))
    assert done.success, done.error
    assert done.new_version == state.version + 1 and done.not_done == []
    s = sm.latest("t_s_two")
    assert s.audio.ambience.get("*") == "rain" and s.audio.bgm_enabled is False


def test_the_part_that_cannot_be_done_is_named_not_dropped(small_project):
    state, sm, agent = _agent(small_project, "t_s_half")
    done = agent.edit(EditCommand(project_id="t_s_half",
                                  query="add rain and change the voices to a deeper tone"))
    assert done.success, done.error
    assert len(done.not_done) == 1 and "deeper tone" in done.not_done[0] and "on its own" in done.not_done[0]
    assert "not done" in done.message


def test_a_sentence_where_nothing_is_understood_is_refused_as_before(small_project):
    state, sm, agent = _agent(small_project, "t_s_none")
    done = agent.edit(EditCommand(project_id="t_s_none", query="make it better and make it nicer"))
    assert not done.success and sm.latest("t_s_none").version == state.version
