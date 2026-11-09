"""pytest configuration: ensure project root is on sys.path."""
import os
import sys
from pathlib import Path

import pytest

# Project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force the offline providers so tests are deterministic and never hit the network:
# mock LLM (template script + keyword classifier) and the PIL placeholder for images.
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("PROVIDER_IMAGE", "placeholder")

# Real credentials must never reach the tests: they would spend the owner's free
# quota and make results depend on which keys happen to be present on a machine.
os.environ["PIPELINE_SKIP_DOTENV"] = "1"      # main.py skips loading .env

# MoviePy (shared/finish.py) runs load_dotenv(find_dotenv()) when it is first imported, which
# would put the whole of a developer's .env into the process the first time a test touched it
# (DST-41). Import it now and take out what the import added; unlike the list below this also
# covers the storage, sign-in and voice settings a .env holds.
_before_moviepy = set(os.environ)
try:
    import moviepy  # noqa: F401
except ImportError:         # the effects tests skip themselves without it
    pass
for _added in set(os.environ) - _before_moviepy:
    os.environ.pop(_added, None)
for _credential in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
                    "POLLINATIONS_API_KEY", "AGNES_API_KEY", "ELEVENLABS_API_KEY", "FAL_KEY",
                    "FAL_API_KEY",
                    "REPLICATE_API_TOKEN", "HF_TOKEN", "HUGGINGFACE_API_KEY",
                    "OLLAMA_HOST", "SD_API_URL", "LOCAL_SD", "MYMEMORY_EMAIL",
                    # Tracing: a test run must not send traces anywhere.
                    "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL",
                    "LANGFUSE_HOST",
                    # Alerts: a test run must not email anyone.
                    "MONITOR_PING_BASE", "MONITORING_FILE"):
    os.environ.pop(_credential, None)
os.environ.pop("DATABASE_URL", None)

# Point the suite at a real server to prove the SQL works there, e.g.
#   TEST_DATABASE_URL=postgresql+psycopg://storygen:storygen@localhost:55432/storygen
# Without it every test gets its own throwaway SQLite file, as before. The
# tables are emptied between tests, so a shared server behaves like one.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
# Only the languages a test asks for get subtitle tracks.
os.environ.pop("SUBTITLE_EXTRA_LANGUAGES", None)

# Register all MCP tools.
import mcp.tools  # noqa: F401, E402


# The tests that took 5 s or more when the suite was timed (4 Nov 2026, one run of
# the whole suite on a laptop; together 327 of 567 s, 58%): they build a rendered
# film and run it through edits, voices or the whole pipeline. A pull request skips
# them (`-m "not slow"`, to stay inside the free GitHub plan's monthly minutes); the
# full run (`pytest -q`, which Gate 1 requires before every commit, and the weekly
# and on-demand full-matrix workflow) runs them. "file::function": every parameter of
# a function is included. Add a heavy new test here, so it is chosen and not forgotten.
# DST-35 (5 Nov 2026): films now render at 1080p, which slowed every test that renders
# at the default size (the whole suite, 598 s to 742 s on that laptop); the six
# functions below it that crossed 5 s were added then.
SLOW = frozenset({
    "tests/integration/test_pipeline_end_to_end.py::test_full_pipeline_produces_video",
    "tests/unit/test_eval_testset.py::test_a_testset_run_plans_every_prompt_and_renders_the_subset",
    "tests/unit/test_m1_backend.py::test_pipeline_emits_started_and_complete_per_phase",
    "tests/unit/test_m1_edits.py::test_edits_keep_multilang_subtitles_and_speed",
    "tests/unit/test_m1_edits.py::test_revert_restores_previous_video",
    "tests/unit/test_m1_edits.py::test_snapshot_contains_every_referenced_file",
    "tests/unit/test_m1_edits.py::test_voice_edit_keeps_character_voice_and_applies_tone",
    "tests/unit/test_m3_consistency.py::test_change_character_design_sticks_across_later_renders",
    "tests/unit/test_m3_storyboard.py::test_benchmark_runs_offline_and_reports",
    "tests/unit/test_m8_edit_jobs.py::test_a_cancelled_edit_puts_its_files_back",
    "tests/unit/test_m8_edit_jobs.py::test_a_revert_is_saved_as_the_version_it_is",
    "tests/unit/test_non_english_voices.py::test_a_film_within_the_tolerance_is_not_flagged",
    "tests/unit/test_m3_storyboard.py::test_edits_before_rendering_reach_the_film",
    "tests/unit/test_m3_storyboard.py::test_render_turns_an_approved_storyboard_into_a_film",
    "tests/unit/test_m3_storyboard.py::test_storyboard_api_round_trip",
    "tests/unit/test_m8_creator_api.py::test_the_cards_name_the_voices_of_the_engine_about_to_render",
    "tests/unit/test_m8_edit_jobs.py::test_a_storyboard_cannot_be_edited_like_a_film",
    "tests/unit/test_m8_edit_jobs.py::test_an_edit_is_queued_rather_than_run_in_the_request",
    "tests/unit/test_m8_edit_jobs.py::test_an_edit_runs_on_a_worker_and_says_what_it_understood",
    "tests/unit/test_m8_edit_jobs.py::test_an_edit_starts_from_the_saved_files",
    "tests/unit/test_m8_edit_jobs.py::test_going_back_restores_the_film_exactly",
    "tests/unit/test_m8_edit_jobs.py::test_the_classic_page_still_gets_its_answer_in_the_response",
    "tests/unit/test_m8_voice_edits.py::test_a_character_edit_keeps_that_characters_scene_only_voice",
    "tests/unit/test_m8_voice_edits.py::test_a_rewritten_script_records_fresh_in_the_same_engine",
    "tests/unit/test_m8_voice_edits.py::test_a_scene_voice_change_survives_a_later_global_edit",
    "tests/unit/test_m8_voice_edits.py::test_an_unknown_tone_fails_instead_of_doing_nothing",
    "tests/unit/test_m8_voice_edits.py::test_regenerating_the_audio_keeps_every_voice_and_the_engine",
    "tests/unit/test_m8_voice_edits.py::test_scene_voice_changes_are_kept_in_the_saved_state",
    "tests/unit/test_m8_voice_edits.py::test_the_same_scene_edit_twice_moves_on_instead_of_repeating",
    "tests/unit/test_m9_parallel_shots.py::test_a_render_runs_shots_side_by_side",
    "tests/unit/test_m9_parallel_shots.py::test_one_worker_renders_one_shot_at_a_time",
    "tests/unit/test_non_english_voices.py::test_a_film_off_its_target_is_flagged",
    "tests/unit/test_storyboard_redraw.py::test_a_scene_that_never_got_a_picture_is_not_marked_as_having_one",
})


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Give the tests listed in SLOW the `slow` marker, before `-m` filters on it."""
    for item in items:
        if item.nodeid.split("[")[0] in SLOW:
            item.add_marker(pytest.mark.slow)


@pytest.hookimpl(trylast=True, specname="pytest_collection_modifyitems")
def pytest_deal_out_slices(config, items):
    """TEST_SHARD=N/M keeps the Nth of M slices of the suite, for CI (DST-31).

    It runs after `-m` has filtered, so the slices are dealt out over what is
    actually going to run. CI runs the M slices as separate jobs, each on its own
    machine, so the suite takes about 1/M of the time. Tests are dealt out in turn
    after sorting by id, so the slices are equal in size, the same on every machine,
    and each file's tests are spread across all of them (a file's tests are about
    equally heavy). Each slice still runs its tests one at a time, as a local run
    does, so tests that assert a wall-clock bound see a quiet machine. Unset,
    nothing changes.
    """
    spec = os.environ.get("TEST_SHARD", "").strip()
    if not spec:
        return
    try:
        number, total = (int(part) for part in spec.split("/"))
        if not 1 <= number <= total:
            raise ValueError
    except ValueError:
        raise pytest.UsageError(
            f"TEST_SHARD must look like 2/4 (slice 2 of 4), not {spec!r}") from None
    by_id = sorted(range(len(items)), key=lambda i: items[i].nodeid)
    mine = {i for place, i in enumerate(by_id) if place % total == number - 1}
    config.hook.pytest_deselected(items=[it for i, it in enumerate(items) if i not in mine])
    items[:] = [it for i, it in enumerate(items) if i in mine]


@pytest.fixture(autouse=True)
def _request_log_in_tmp(tmp_path, monkeypatch):
    """The API notes every request for the monitor (DST-32); keep that out of data/."""
    from shared import monitoring
    monkeypatch.setattr(monitoring, "request_log_dir", lambda: tmp_path / "monitoring")


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Point outputs, snapshots and the database at a temp dir."""
    import shared.constants as constants
    from shared import db
    monkeypatch.setattr(constants, "OUTPUTS_DIR", tmp_path / "out")
    monkeypatch.setattr(constants, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(constants, "DB_PATH", tmp_path / "state.db")
    if TEST_DATABASE_URL:
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        # Tests that name a SQLite file explicitly must land on the server too,
        # or half the suite would quietly keep testing SQLite.
        monkeypatch.setattr(db, "url_for_path", lambda _p: TEST_DATABASE_URL)
    else:
        # A DATABASE_URL in the environment would send the test's writes to a
        # real database; shared.db falls back to constants.DB_PATH without it.
        monkeypatch.delenv("DATABASE_URL", raising=False)
    constants.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    constants.STATE_DIR.mkdir(parents=True, exist_ok=True)
    yield tmp_path
    if TEST_DATABASE_URL:
        _empty_tables(db, TEST_DATABASE_URL)
    # Close the engines holding this tmp_path's SQLite files open, or Windows
    # refuses to delete them.
    db.dispose_all()


def _empty_tables(db, url: str) -> None:
    """Leave a shared server as clean as a fresh temp file."""
    engine = db.get_engine(url)
    with engine.begin() as c:
        for table in reversed(db.metadata.sorted_tables):
            c.execute(table.delete())


def silence_tts(tools) -> list:
    """Patch a ToolExecutor so TTS renders silent audio offline.

    Returns a list that records the kwargs of every audio.tts call.
    """
    calls: list = []
    real = tools.execute

    def patched(tool, **kwargs):
        if tool == "audio.tts":
            calls.append(dict(kwargs))
            kwargs["engine"] = "silent"
        return real(tool, **kwargs)

    tools.execute = patched
    return calls


def signed_in_client(app, email: str = "tester@example.com",
                     password: str = "a-test-passphrase"):
    """A TestClient holding an admin session.

    Since M5 the API needs an account for everything, and the first account
    created is the admin — which is also what the CLI-made projects in these
    tests belong to.
    """
    from fastapi.testclient import TestClient
    client = TestClient(app)
    res = client.post("/api/auth/register",
                      json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return client


def run_queued_jobs(orchestrator=None, limit: int = 10) -> list:
    """Drain the job queue the way a worker process would.

    Runs are queued by the API, not executed by it, so a test that posts to
    /api/pipeline/... has to play the worker.
    """
    from jobs import queue, worker
    ran = []
    for _ in range(limit):
        job = queue.claim("test-worker")
        if job is None:
            break
        worker.run_job(job, orchestrator=orchestrator)
        ran.append(job)
    return ran


@pytest.fixture
def fake_translation(monkeypatch):
    """Offline translator: prefixes each line with the target language."""
    from mcp.tools.llm_tools.translate_tool import TranslateTool
    calls: list = []

    def fake(self, lines, lang):
        calls.append(lang)
        return [f"[{lang}] {line}" for line in lines]

    monkeypatch.setattr(TranslateTool, "_mymemory", fake)
    return calls


@pytest.fixture
def small_project(isolated_dirs):
    """Factory: build a small fully-rendered project (silent audio, 320x180 @ 12 fps)."""
    from agents.audio_agent import AudioAgent
    from agents.story_agent.planner import template_script
    from agents.video_agent import VideoAgent
    from shared.schemas.pipeline import PipelineState
    from state_manager.snapshot import referenced_files
    from state_manager.state_manager import StateManager
    from state_manager.storage import SqliteStorage

    # A small film in each shape: the same 320-pixel long side as the default 16:9 one.
    SIZES = {"16:9": (320, 180), "9:16": (180, 320), "1:1": (240, 240)}

    def build(project_id="t_small", duration_s=24, scenes=3, subtitle_language="English",
              with_subtitles=True, with_bgm=False, burn_subtitles=True,
              format="16:9", style="realistic"):
        sm = StateManager(SqliteStorage(isolated_dirs / "state.db"))
        state = PipelineState(project_id=project_id, user_prompt="A robot learns to paint")
        state.script = template_script(project_id, state.user_prompt,
                                       target_duration_s=duration_s, scene_count=scenes)
        state.script.story.format, state.script.story.style = format, style
        film_w, film_h = SIZES[format]
        audio = AudioAgent()
        silence_tts(audio.tools)
        audio.run(state, with_bgm=with_bgm)
        VideoAgent().run(state, with_subtitles=with_subtitles,
                         subtitle_language=subtitle_language,
                         width=film_w, height=film_h, fps=12, burn_subtitles=burn_subtitles,
                         use_text_to_video=False, use_lip_sync=False, cinematic_post=False)
        # Described the way a real run is, so the history reads it as a film.
        from agents.orchestrator.workflow import INITIAL_RUN
        state.stage = "rendered"
        sm.snapshot(state, asset_paths=referenced_files(state), description=INITIAL_RUN)
        return state, sm

    return build
