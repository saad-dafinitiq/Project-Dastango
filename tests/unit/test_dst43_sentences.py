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


# ---- DST-44 (item 2): the two limits DST-43 left ----------------------------------------------------------

@pytest.mark.parametrize("query, parts", [
    ("make the music and the rain quieter", ["make the music quieter", "make the rain quieter"]),
    ("make the music quieter and the rain louder", ["make the music quieter", "make the rain louder"]),
    ("turn the music down and the sea up", ["turn the music down", "turn the sea up"]),
    ("make the music and the voices quieter", ["make the music quieter", "make the voices quieter"]),
    ("in scene 2, add rain and make the music quieter", ["add rain in scene 2", "make the music quieter in scene 2"]),
    ("in scene 2 add rain", ["in scene 2 add rain"]),
    # unchanged: one request, whatever the "and" is
    ("make the music and the rain", None),
    ("add rain in scene 2 and make the music quieter", ["add rain in scene 2", "make the music quieter"]),
])
def test_two_sounds_for_one_change_are_two_requests_and_a_leading_scene_covers_all(query, parts):
    assert split_request(query) == (parts or [query])


@pytest.mark.parametrize("query, level", [("make the rain quieter", 60), ("turn the wind up", 150),
                                          ("make the sea much louder", 200), ("make the city 50%", 50)])
def test_the_ambience_has_a_level_sentence_too(query, level):
    got = _read(query)
    assert (got.intent, got.parameters["target"], got.parameters["percent"]) == ("set_volume", "ambience", level)


def test_a_level_in_one_scene_is_refused_not_applied_to_the_whole_film_unsaid():
    """The levels are the film's mixer. "quieter in scene 2" used to change the whole film and say nothing."""
    from agents.edit_agent.planner import plan

    with pytest.raises(ValueError, match="whole film"):
        plan(_read("make the music quieter in scene 2"))
    assert plan(_read("make the music quieter"))                 # for the whole film it is fine


def test_a_sentence_naming_two_sounds_changes_both(small_project):
    state, sm, agent = _agent(small_project, "t_s_both")
    state = sm.latest("t_s_both")
    done = agent.edit(EditCommand(project_id="t_s_both", query="make the music and the rain quieter"))
    assert done.success, done.error
    s = sm.latest("t_s_both")
    assert s.audio.music_volume < state.audio.music_volume
    assert s.audio.ambience_volume < state.audio.ambience_volume
    assert done.new_version == state.version + 1


def test_a_scene_named_in_only_one_part_is_said_to_leave_the_other_part_for_the_whole_film(small_project):
    state, sm, agent = _agent(small_project, "t_s_scope")
    done = agent.edit(EditCommand(project_id="t_s_scope", query="add rain in scene 2 and make the music quieter"))
    assert done.success, done.error
    assert done.notes and "whole film" in done.notes[0] and "music quieter" in done.notes[0]
    assert "whole film" in done.message
    s = sm.latest("t_s_scope")
    assert s.audio.ambience.get("scene_2") == "rain" or s.audio.ambience.get(state.script.scenes[1].scene_id) == "rain"


def test_a_leading_scene_that_cannot_apply_to_a_part_says_so_instead_of_widening_it(small_project):
    state, sm, agent = _agent(small_project, "t_s_lead")
    done = agent.edit(EditCommand(project_id="t_s_lead", query="in scene 2, add rain and make the music quieter"))
    assert done.success, done.error
    assert any("music quieter" in n and "whole film" in n for n in done.not_done), done.not_done
    assert sm.latest("t_s_lead").audio.music_volume == state.audio.music_volume      # the music was left alone
