"""DST-39 phase 2: the music library, uploads, ambience and the mix, as edits.

What must hold: every audio edit leaves the master exactly as long as the timeline (so the
film stays in sync), it is a version that "Go back" undoes, and a click and a typed sentence
are the same operation.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from agents.edit_agent import EditAgent, ops
from agents.edit_agent.intent_classifier import classify
from agents.edit_agent.planner import plan
from shared import music
from shared.schemas.audio import AudioOutput, TimingManifest
from shared.schemas.edit import EditCommand
from tests.conftest import signed_in_client, silence_tts


def _seconds(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def _mean_volume(path, start=None, dur=None) -> float:
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", str(start), "-t", str(dur)]
    cmd += ["-i", str(path), "-af", "volumedetect", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    m = re.search(r"mean_volume: (-?[\d.inf]+) dB", err)
    return float("-inf") if not m or m.group(1).endswith("inf") else float(m.group(1))


def _tone(path: Path, seconds: float = 4.0, freq: int = 330) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}", str(path)], check=True)
    return path


# ---- the library ----------------------------------------------------------------------------

def test_the_library_has_two_tracks_for_every_mood_and_each_has_a_licence_line():
    lib = music.library()
    assert len(lib) >= 20
    for mood in ("ambient", "neutral", "tense", "ominous", "mysterious", "sad", "joyful",
                 "energetic", "epic", "ethereal"):
        assert sum(1 for t in lib.values() if t.mood == mood) >= 2, mood
    licences = (music.LIBRARY_DIR / "LICENSES.md").read_text(encoding="utf-8")
    for t in lib.values():
        assert t.id in licences and t.licence == "CC0 1.0", t.id
        assert t.path.stat().st_size < 600_000                               # a repo-friendly size


def test_a_mood_a_writing_model_invents_still_finds_music():
    assert music.resolve_mood("melancholic") == "sad"
    assert music.resolve_mood("Triumphant") == "epic"
    assert music.resolve_mood("sideways") == "ambient"
    assert music.for_mood("tense", 0).mood == "tense"
    assert music.for_mood("tense", 0).id != music.for_mood("tense", 1).id    # scenes vary


def test_a_scene_plays_its_own_choice_then_the_films_then_its_moods_track():
    class S:
        def __init__(self, sid, mood="tense"):
            self.scene_id, self.music_mood = sid, mood

    a = AudioOutput(voice_configs=[], manifest=TimingManifest(project_id="x", total_duration_ms=1000))
    assert music.choice_for_scene(a, S("scene_1"), 0).startswith("tense-")
    a.music_track = "epic-1"
    assert music.choice_for_scene(a, S("scene_1"), 0) == "epic-1"
    a.scene_music["scene_1"] = "sad-2"
    assert music.choice_for_scene(a, S("scene_1"), 0) == "sad-2"
    a.scene_music["scene_1"] = "none"
    assert music.choice_for_scene(a, S("scene_1"), 0) is None


def test_a_bed_is_cut_to_the_millisecond_and_a_track_carries_on_across_scenes(tmp_path):
    src = music.library()["ambient-1"].path
    bed = music.cut_bed(src, 41_000, 3_210, tmp_path / "bed.wav")            # past the end: it loops
    assert abs(_seconds(bed) - 3.21) < 0.02
    silent = music.silence(1_500, tmp_path / "s.wav")
    joined = music.join([bed, silent], tmp_path / "j.wav")
    assert abs(_seconds(joined) - 4.71) < 0.03


# ---- the mix --------------------------------------------------------------------------------

def test_voices_and_the_music_levels_move_the_balance(tmp_path):
    from mcp.tool_executor import ToolExecutor
    tools = ToolExecutor()
    voice = _tone(tmp_path / "v.wav", 3, 220)
    bed = _tone(tmp_path / "m.wav", 3, 880)
    levels = {}
    for name, vol, bgm_vol in (("base", 1.0, 0.32), ("quiet_voice", 0.5, 0.32), ("loud_music", 1.0, 0.96)):
        res = tools.execute("audio.merge", out_path=str(tmp_path / f"{name}.wav"), placements=[{"path": str(voice), "start_ms": 0}],
                            total_ms=3000, bgm=str(bed), bgm_volume=bgm_vol, dialogue_volume=vol, duck=False, loudness=False)
        assert res.success, res.error
        levels[name] = _mean_volume(res.data)
    assert levels["quiet_voice"] < levels["base"] - 3                         # about 6 dB lower
    assert levels["loud_music"] > levels["base"] + 1.5          # a third voice sine already in the mix


def test_ambience_is_mixed_in_even_with_no_music(tmp_path):
    from mcp.tool_executor import ToolExecutor
    voice = _tone(tmp_path / "v.wav", 3, 220)
    rain = music.ambience_bed("rain", 3000, tmp_path / "rain.wav")
    plain = ToolExecutor().execute("audio.merge", out_path=str(tmp_path / "plain.wav"), placements=[{"path": str(voice), "start_ms": 0}],
                                   total_ms=3000, ambience=str(rain), ambience_volume=0.0, duck=False, loudness=False)
    wet = ToolExecutor().execute("audio.merge", out_path=str(tmp_path / "wet.wav"), placements=[{"path": str(voice), "start_ms": 0}],
                                 total_ms=3000, ambience=str(rain), ambience_volume=0.6, duck=False, loudness=False)
    assert plain.success and wet.success and wet.metadata["ambience"] is True
    assert _mean_volume(wet.data) > _mean_volume(plain.data)
    assert abs(_seconds(wet.data) - 3.0) < 0.05


def test_every_ambience_kind_renders_to_the_length_asked(tmp_path):
    for kind in music.AMBIENCE:
        assert abs(_seconds(music.ambience_bed(kind, 2_345, tmp_path / f"{kind}.wav")) - 2.345) < 0.03, kind
    with pytest.raises(music.MusicError):
        music.ambience_bed("lava", 1000, tmp_path / "x.wav")


# ---- the operations, on a rendered film ----------------------------------------------------

def _agent(small_project, pid, with_bgm=False):
    state, sm = small_project(project_id=pid, with_bgm=with_bgm)
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    return state, sm, agent


def _in_sync(sm, pid):
    s = sm.latest(pid)
    master = _seconds(s.audio.master_track)
    assert abs(master - s.audio.manifest.total_duration_ms / 1000) < 0.06, (master, s.audio.manifest.total_duration_ms)
    return s


def test_music_edits_keep_the_master_as_long_as_the_timeline(small_project):
    state, sm, agent = _agent(small_project, "t_mus")
    for op, params in (("set_music", {"mood": "epic"}),
                       ("set_music", {"scene": "2", "track": "sad-1"}),
                       ("set_music", {"scene": "3", "off": True}),
                       ("set_ambience", {"kind": "rain"}),
                       ("set_volume", {"target": "music", "percent": 40}),
                       ("set_volume", {"target": "voices", "percent": 150}),
                       ("set_music", {"reset": True})):
        done = agent.apply_op("t_mus", op, params)
        assert done.success, (op, params, done.error)
        s = _in_sync(sm, "t_mus")
        assert s.video.final_video_path
    s = sm.latest("t_mus")
    assert s.audio.music_volume == pytest.approx(0.128) and s.audio.voice_volume == 1.5
    assert s.audio.scene_music.get("scene_3") is None and s.audio.bgm_enabled
    assert s.audio.ambience == {"*": "rain"}


def test_a_scene_set_to_no_music_is_quiet_there_and_only_there(small_project):
    state, sm, agent = _agent(small_project, "t_mute", with_bgm=True)
    assert agent.apply_op("t_mute", "set_music", {"scene": "2", "off": True}).success
    s = sm.latest("t_mute")
    slot = s.audio.manifest.scene("scene_2")
    bed = s.audio.bgm_track
    quiet = _mean_volume(bed, slot.start_ms / 1000 + 0.3, max(0.2, slot.duration_ms / 1000 - 0.6))
    other = s.audio.manifest.scene("scene_1")
    heard = _mean_volume(bed, other.start_ms / 1000 + 0.5, 1.0)
    assert quiet < -60 and heard > -45


def test_a_scene_with_its_own_ambience_gets_it_only_there(small_project):
    state, sm, agent = _agent(small_project, "t_amb")
    assert agent.apply_op("t_amb", "set_ambience", {"scene": "1", "kind": "wind"}).success
    s = sm.latest("t_amb")
    assert s.audio.ambience == {"scene_1": "wind"} and s.audio.ambience_track
    one, two = s.audio.manifest.scene("scene_1"), s.audio.manifest.scene("scene_2")
    assert _mean_volume(s.audio.ambience_track, one.start_ms / 1000 + 0.4, 1.0) > -60
    assert _mean_volume(s.audio.ambience_track, two.start_ms / 1000 + 0.2, 1.0) < -80
    removed = agent.apply_op("t_amb", "set_ambience", {"kind": "none"})
    assert removed.success and sm.latest("t_amb").audio.ambience_track is None


def test_bad_audio_edits_are_refused_with_the_choices_and_change_nothing(small_project):
    state, sm, agent = _agent(small_project, "t_bad")
    for op, params, hint in (("set_music", {"mood": "polka"}, "which mood"),
                             ("set_music", {"track": "nope-9"}, "no track"),
                             ("set_music", {"track": "upload:abcd1234"}, "uploaded track"),
                             ("set_music", {}, "what about the music"),
                             ("set_ambience", {"kind": "lava"}, "which ambience"),
                             ("set_volume", {"target": "music", "percent": 999}, "")):
        with pytest.raises(ops.OpError, match=hint or "."):
            ops.check(state, op, params)
    assert sm.latest("t_bad").version == state.version


def test_an_audio_edit_is_a_version_and_going_back_undoes_it(small_project):
    state, sm, agent = _agent(small_project, "t_mver")
    master = _seconds(state.audio.master_track)
    done = agent.apply_op("t_mver", "set_ambience", {"kind": "city"})
    assert done.success and done.new_version == state.version + 1
    assert sm.history("t_mver")[-1]["edit_intent"]["query"].startswith("The film: far-off traffic")
    sm.revert("t_mver", state.version)
    back = sm.latest("t_mver")
    assert back.audio.ambience == {} and back.audio.ambience_track is None
    assert abs(_seconds(back.audio.master_track) - master) < 0.06


def test_music_before_a_render_changes_the_moods_but_levels_need_the_film():
    from agents.story_agent.planner import template_script
    from shared.schemas.pipeline import PipelineState
    st = PipelineState(project_id="t_pre", user_prompt="x")
    st.script = template_script("t_pre", "x", target_duration_s=15, scene_count=2)
    assert ops.apply(st, "set_music", {"scene": "2", "mood": "ominous"}) == "Scene 2's music: ominous"
    assert st.script.scenes[1].music_mood == "ominous"
    with pytest.raises(ops.OpError, match="render the film first"):
        ops.apply(st, "set_volume", {"target": "music", "percent": 50})


# ---- sentences -----------------------------------------------------------------------------

SENTENCES = [
    ("make the music quieter",                   "set_volume",  {"target": "music", "percent": 60}),
    ("make the music much louder",               "set_volume",  {"target": "music", "percent": 200}),
    ("turn the music down to 25%",               "set_volume",  {"target": "music", "percent": 25}),
    ("mute the music in scene 2",                "set_music",   {"off": True}),
    ("no music in scene 3",                      "set_music",   {"off": True}),
    ("change the music to something tense",      "set_music",   {"mood": "tense"}),
    ("make the music in scene 2 sad",            "set_music",   {"mood": "sad"}),
    ("play Horizon as the music",                "set_music",   {"track": "epic-1"}),
    ("put the music back to how it was",         "set_music",   {"reset": True}),
    ("add rain to scene 1",                      "set_ambience", {"kind": "rain"}),
    ("add the sound of the sea",                 "set_ambience", {"kind": "sea"}),
    ("remove the rain",                          "set_ambience", {"kind": "none"}),
    ("add background music tense",               "add_background_music", {"mood": "tense"}),
    ("remove background music",                  "remove_background_music", {}),
]


@pytest.mark.parametrize("sentence,intent,expected", SENTENCES)
def test_a_sentence_becomes_the_operation(sentence, intent, expected):
    got = classify(sentence, scenes=["scene_1", "scene_2", "scene_3"])
    assert got.intent == intent, got
    for k, v in expected.items():
        assert got.parameters.get(k) == v, (sentence, got.parameters)


def test_the_older_music_intents_run_as_the_shared_operation():
    steps = plan(classify("add background music tense", scenes=["scene_1"]))
    assert [s.name for s in steps] == ["set_music", "remix_audio", "recompose_video"]
    assert steps[0].params == {"mood": "tense"}
    off = plan(classify("remove background music", scenes=["scene_1"]))
    assert off[0].name == "set_music" and off[0].params == {"off": True}


def test_asking_for_a_level_without_saying_which_asks_instead_of_guessing():
    got = classify("make it better", scenes=["scene_1"])
    assert got.intent in ("generic_edit", "unclear")


def test_a_click_and_a_sentence_give_the_same_music(small_project):
    _a, sm_a, agent_a = _agent(small_project, "t_mclick")
    _b, sm_b, agent_b = _agent(small_project, "t_mwords")
    assert agent_a.apply_op("t_mclick", "set_volume", {"target": "music", "percent": 60}).success
    typed = agent_b.edit(EditCommand(project_id="t_mwords", query="make the music quieter"))
    assert typed.success, typed.error
    a, b = sm_a.latest("t_mclick").audio, sm_b.latest("t_mwords").audio
    assert a.music_volume == b.music_volume == pytest.approx(0.192)
    assert abs(_seconds(a.master_track) - _seconds(b.master_track)) < 0.01


# ---- uploads -------------------------------------------------------------------------------

def test_an_upload_is_checked_and_kept_as_a_normalised_copy(tmp_path):
    src = _tone(tmp_path / "my song.wav", 4)
    rec = music.store_upload(src, "my song.wav", tmp_path / "up")
    assert rec["title"] == "my song" and abs(rec["duration_s"] - 4) < 0.1
    assert Path(rec["path"]).exists() and Path(rec["path"]).suffix == ".mp3"
    assert abs(_seconds(rec["path"]) - 4) < 0.2


@pytest.mark.parametrize("name,make,hint", [
    ("notes.txt", lambda p: p.write_bytes(b"hello"), "use an audio file"),
    ("empty.mp3", lambda p: p.write_bytes(b""), "empty"),
    ("junk.mp3", lambda p: p.write_bytes(b"not audio at all" * 50), "could not be read|no audio"),
    ("short.wav", lambda p: _tone(p, 1), "at least"),
])
def test_a_bad_upload_is_refused_with_a_reason(tmp_path, name, make, hint):
    src = tmp_path / ("src_" + name)
    make(src)
    with pytest.raises(music.MusicError, match=hint):
        music.store_upload(src, name, tmp_path / "up")
    assert not list((tmp_path / "up").glob("*.mp3")) if (tmp_path / "up").exists() else True


def test_a_video_or_an_oversized_or_overlong_file_is_refused(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=3:s=64x64:r=5",
                    "-f", "lavfi", "-i", "sine=d=3", "-shortest", str(video)], check=True)
    with pytest.raises(music.MusicError, match="use an audio file"):                   # not an audio extension
        music.store_upload(video, "clip.mp4", tmp_path / "up")
    disguised = tmp_path / "clip.mp3"
    disguised.write_bytes(video.read_bytes())
    with pytest.raises(music.MusicError, match="video"):
        music.store_upload(disguised, "clip.mp3", tmp_path / "up")
    song = _tone(tmp_path / "s.wav", 4)
    monkeypatch.setattr(music, "MAX_UPLOAD_BYTES", 1000)
    with pytest.raises(music.MusicError, match="limit"):
        music.store_upload(song, "s.wav", tmp_path / "up")
    monkeypatch.setattr(music, "MAX_UPLOAD_BYTES", 15 * 1024 * 1024)
    monkeypatch.setattr(music, "MAX_UPLOAD_S", 2.0)
    with pytest.raises(music.MusicError, match="limit"):
        music.store_upload(song, "s.wav", tmp_path / "up")


def test_an_uploaded_track_plays_in_the_film_and_belongs_to_that_film_only(small_project, tmp_path):
    state, sm, agent = _agent(small_project, "t_up")
    rec = music.store_upload(_tone(tmp_path / "mine.wav", 5), "mine.wav", music.uploads_dir("t_up"))
    done = agent.apply_op("t_up", "set_music", {"track": f"upload:{rec['id']}"})
    assert done.success, done.error
    s = _in_sync(sm, "t_up")
    assert s.audio.music_track == f"upload:{rec['id']}" and s.audio.uploads[0].title == "mine"
    assert any(f"upload:{rec['id']}" == row["id"] for row in music.catalogue(s.audio.uploads))
    other_state, other_sm = small_project(project_id="t_other")
    with pytest.raises(ops.OpError, match="not in this film"):
        ops.check(other_state, "set_music", {"track": f"upload:{rec['id']}"})
    with pytest.raises(music.MusicError):
        music.load_upload("t_up", "../../etc")


# ---- the API -------------------------------------------------------------------------------

@pytest.fixture
def api(small_project, monkeypatch):
    from backend import app as app_module
    from backend.routes import edit as edit_routes
    from backend.routes import pipeline as pipeline_routes
    from backend.services import pipeline_service

    state, sm = small_project(project_id="t_mapi")
    monkeypatch.setattr(pipeline_routes, "sm", sm)
    monkeypatch.setattr(edit_routes, "sm", sm)
    orch = pipeline_service.orchestrator()
    monkeypatch.setattr(orch, "sm", sm)
    monkeypatch.setattr(orch.editor, "sm", sm)
    return state, sm, signed_in_client(app_module.app)


def test_the_catalogue_names_every_track_with_its_licence_and_the_upload_limits(api):
    _s, _sm, client = api
    body = client.get("/api/edit/music").json()
    assert len(body["tracks"]) >= 20 and {"id", "mood", "licence", "seconds"} <= set(body["tracks"][0])
    assert body["limits"]["max_mb"] == 15 and {a["id"] for a in body["ambience"]} >= {"rain", "wind"}
    ops_body = client.get("/api/edit/ops").json()["ops"]
    assert {"set_music", "set_volume", "set_ambience"} <= {o["name"] for o in ops_body}


def test_uploading_needs_the_rights_box_and_a_real_audio_file(api, tmp_path):
    _s, _sm, client = api
    song = _tone(tmp_path / "ok.wav", 4)
    url = "/api/edit/t_mapi/music"
    no_rights = client.post(url, files={"file": ("ok.wav", song.read_bytes())}, data={"rights": "false"})
    assert no_rights.status_code == 400 and "right" in no_rights.json()["detail"]
    junk = client.post(url, files={"file": ("x.mp3", b"nope" * 100)}, data={"rights": "true"})
    assert junk.status_code == 400
    ok = client.post(url, files={"file": ("ok.wav", song.read_bytes())}, data={"rights": "true"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["track"].startswith("upload:") and body["title"] == "ok"
    # not used until an edit says so
    state = _sm.latest("t_mapi")
    assert state.audio.music_track is None
    queued = client.post("/api/edit/t_mapi/op", json={"op": "set_music", "params": {"track": body["track"], "scene": "2"}})
    assert queued.status_code in (200, 202), queued.text


def test_an_oversized_upload_stops_at_the_limit_and_a_stranger_cannot_upload(api, monkeypatch, tmp_path):
    _s, _sm, client = api
    monkeypatch.setattr(music, "MAX_UPLOAD_BYTES", 2048)
    big = client.post("/api/edit/t_mapi/music", files={"file": ("big.wav", b"\0" * 5000)}, data={"rights": "true"})
    assert big.status_code == 413
    from fastapi.testclient import TestClient
    from backend import app as app_module
    anon = TestClient(app_module.app)
    assert anon.post("/api/edit/t_mapi/music", files={"file": ("a.wav", b"x")}, data={"rights": "true"}).status_code == 401
    assert anon.get("/api/edit/music").status_code == 401


@pytest.mark.parametrize("offset_ms", [0, 7_214, 18_460, 39_000])
def test_a_bed_is_audible_wherever_in_the_track_it_starts(tmp_path, offset_ms):
    """Found by a creator: scene 3 began 18 s into its track and came out silent, because the fades
    were timed from the seek offset instead of the start of the bed."""
    bed = music.cut_bed(music.library()["joyful-1"].path, offset_ms, 9_426, tmp_path / "bed.wav",
                        fade_in_ms=350, fade_out_ms=350)
    assert _mean_volume(bed) > -25
    assert _mean_volume(bed, 4, 2) > -25                                    # the middle of it, not just the ends


def test_every_ambience_is_as_loud_as_the_music_so_100_percent_is_audible(tmp_path):
    """Found by a creator: city ambience at 165% could not be heard. The beds were 20 to 40 dB
    under the music (night was at -53 dB) before their volume was even applied."""
    for kind in music.AMBIENCE:
        bed = music.ambience_bed(kind, 6_000, tmp_path / f"{kind}.wav")
        assert -17 < _mean_volume(bed) < -10, kind
