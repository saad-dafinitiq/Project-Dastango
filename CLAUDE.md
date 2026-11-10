# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository, and the
engineering log of every milestone. For an overview, start with
[README.md](README.md) and [docs/](docs/).

## What this is

**Dastango** — an agentic pipeline that turns one natural-language prompt into
a short animated film, with natural-language editing and versioned undo —
deployed at https://139-185-59-132.sslip.io. Internal names (database, user,
containers, paths) still say `storygen`; only what people see says Dastango.
The user guide is the web app's `/docs` pages; the interactive API reference
moved to `/api/docs`.

Pipeline: **Phase 1 Story** → **Phase 2 Audio** → **Phase 3 Video**, the
**creator interface** (Next.js static export served by FastAPI), and
**Edit & Undo**.

## Working agreement

- Work is split into milestones (M0 baseline → M1 correctness → M2 model settings layer
  → M3 quality → M4 production architecture → M5 accounts → M6 premium video → M7 ship
  polish → M8 creator interface → M9 deployment); see [CHANGELOG.md](CHANGELOG.md).
- **At the end of every milestone: commit, push, open a draft PR, report back, and
  wait for the owner's go-ahead before starting the next milestone.**
- Current constraint: **$0 spend.** Use the best free API tiers and open-source
  models. Paid providers (Claude, Seedance, etc.) come later, so keep every model
  choice swappable through configuration, not hard-coded in agent logic.
- The dev machine is Windows 11 with an RTX 3050 Laptop GPU (6 GB VRAM) and 16 GB RAM.
  Heavy video models (Wan 2.2, lip-sync) must run on free cloud GPUs, not locally.

## Setup

```bash
pip install uv                                    # once
uv sync                  # creates .venv from uv.lock (exact versions); optional parts: --extra voices|s3|postgres|tracing
# optional local Stable Diffusion (pulls PyTorch, outside the lock): see docs/DEVELOPMENT.md
cp .env.example .env    # all keys optional; runs in mock/template mode without them
```

`ffmpeg` and `ffprobe` must be on PATH (Windows: `winget install Gyan.FFmpeg`).
The default TTS engine shells out to the `edge-tts` CLI, which lives in
`.venv/Scripts`, so activate the venv (or put it on PATH) before running.

## Commands

```bash
python -m pytest -q                        # full suite (~3 min, offline: mock LLM + placeholder images)
python -m pytest -q -m "not slow"          # what a pull request runs: all but the SLOW list in tests/conftest.py
TEST_SHARD=2/2 python -m pytest -q -m "not slow"   # one pull-request CI job: slice 2 of 2
TEST_DATABASE_URL=postgresql+psycopg://... python -m pytest -q   # same suite, real Postgres
TEST_S3_ENDPOINT=http://127.0.0.1:5111 python -m pytest -k real_s3   # against a real S3/R2 server
python -m pytest tests/unit/test_phase5_edit.py -q
python main.py "prompt" --duration 30 --scenes 4   # CLI end-to-end run
python main.py plan "prompt" --scenes 4            # storyboard only (cheap)
python main.py storyboard|restyle|render <pid>     # review, edit, then render
python scripts/benchmark.py --offline              # fixed prompts, measured
python scripts/benchmark.py --testset eval/testset.jsonl --render-subset   # labelled test set (eval/README.md)
python main.py providers                   # which providers are detected
python main.py serve --reload              # web UI on http://localhost:8000 (runs a worker too)
cd web && npm ci && npm run build          # build the creator UI; serve then uses it at /
cd web && npm run dev                      # UI with hot reload on :3000, API proxied to :8000
python main.py worker                      # a worker process on its own (WORKER_INLINE=0 for the API)
python main.py monitor --once              # every live-site check's state, sends nothing (docs/MONITORING.md)
python main.py jobs [--status failed]      # the queue: what ran, what broke
python main.py users [create <email> --admin | passwd <email> | role <email> admin]
docker compose up --build --scale worker=3 # API + workers + Postgres
python main.py edit <project_id>           # interactive edit REPL
python main.py list | history <project_id>
```

Outputs go to `data/outputs/<project_id>/`, version snapshots to
`data/state_versions/`, and the version log to `data/state.db` (all gitignored).
`DATA_DIR=/somewhere` moves all three — a throwaway instance for testing never
touches the real films (state records absolute paths, so copying `data/` is
not enough).

## Architecture map

| Area | Path | Notes |
|---|---|---|
| Cross-phase contract | `shared/schemas/` | Pydantic models. `PipelineState` is the object passed between phases and versioned. Change these carefully: every phase depends on them. |
| Orchestrator | `agents/orchestrator/` | Plain-Python graph (not LangGraph) running phase1 → phase2 → phase3, emitting `ProgressEvent`s. |
| Phase 1 | `agents/story_agent/` | LLM structured output → `ScriptOutput`; `planner.py` is the deterministic template used when the LLM is `mock`; `appearance.py` locks each character's look + image seed; `cast.py` (DST-33) leaves out anyone with no lines before anything reads the cast, after one repair for a silent character the creator named, and the prompt sizes the cast to the film (`shared/timeline.py` `cast_limit`). |
| Storyboard | `shared/schemas/storyboard.py` + orchestrator `plan`/`update_storyboard`/`render` | The cheap draft (script + one preview image per scene) that is reviewed and edited before the expensive render. `PipelineState.stage` is draft → storyboard → rendered. |
| Timeline | `shared/timeline.py` | **Single source of truth for timing.** Audio places lines on it, video cuts on its boundaries (absolute ms → frames), subtitles read it. Never time video or audio independently. |
| Phase 2 | `agents/audio_agent/` | `render_line` (TTS) → `retime` (timeline) → `remix` (per-scene BGM + master with lines placed at `start_ms`). Edits reuse these. |
| Coverage | `agents/video_agent/coverage.py` | **How a scene is shot (DST-36):** 2 to 4 shots per scene, a wide that opens, a cut to whoever speaks, a pull-out that closes it. Pure planning, called by the renderer and by the storyboard cards, so both show the same shots. A medium is a crop of the wide picture, a close-up the speaker's portrait; only a long scene draws one more picture. Numbers in `config/video.yaml` (`coverage`, `handheld`). |
| Film format and style | `shared/film_format.py`, `StoryOutput.format` / `.style`, `agents/story_agent/visual_style.py`, `agents/story_agent/scenes.py` | **What the creator chose before planning (DST-34).** Format (16:9, 9:16, 1:1) fixes the film's size (1920x1080, 1080x1920, 1080x1080), the picture size (1280 long side; a square at the providers' own 1024), the subtitle layout and the camera (a tall frame tilts and alternates its medium shots up and down). Style (realistic or anime) is a lock: its medium is said first in every picture prompt and the story's own palette text may not name another medium. Both live on the script's story, which every image path already receives, so previews, portraits, shots, edits that redraw and fallback providers cannot disagree. `scenes.py` holds the script to the scene count asked for. A film made before has the defaults. |
| Editing operations | `agents/edit_agent/ops.py`, `mcp/tools/edit_tools/`, `shared/transitions.py`, `shared/xfade.py` | **One set of edits for three callers (DST-39).** Each operation is a validated parameter model plus an `apply` that changes the script and says what it did in words; the executor runs it as a step, then re-cuts the film. A click (`POST /api/edit/{id}/op`), a typed sentence (classifier -> planner -> the same step) and the video agent's defaults (`coverage.transition_into`) therefore cannot differ. Also registered as `edit.<name>` tools. All the operations are listed in the next row and in `docs/API.md`; the first two are `set_transition` and `set_camera`. **Transitions** are ffmpeg `xfade` styles (the zoom is a scale push joined by a fade, DST-43), one per cut: a cut inside a scene is `ShotOverride.transition`, the cut between scenes is `Scene.cut_in`. A transition starts on the cut and runs past it, so the shot before the cut is rendered with the overlap as extra frames and the film stays exactly the timeline's length; a straight cut is joined with `concat` (no re-encode). Defaults: cuts inside a scene, a 400 ms fade between scenes; a film rendered before keeps its old 200/400 ms fades (`VideoOutput.legacy_transitions`) and its scene cache. |
| Sound and titles (DST-39) | `shared/music.py`, `assets/music/`, `scripts/make_music.py`, `shared/graphics.py`, `assets/fonts/`, `AudioOutput` mix fields, `StoryOutput.graphics` | **Music** is a library of 20 original CC0 tracks (two per mood, `assets/music/library.json` + `LICENSES.md`, synthesised by `scripts/make_music.py`, reproducible) plus per-film uploads; a scene plays its own choice, else the film's, else its mood's track, cut from where the film's clock is so a track carries on across scenes. **Ambience** is ffmpeg noise (rain, wind, city, sea, night). `AudioOutput.music_volume / voice_volume / ambience_volume` are "100%" at 0.32 / 1.0 / 0.22 and are multipliers on levels set relative to the dialogue (DST-41): every line is brought to one loudness, the music bed sits about 13 dB under a line and the ambience about 18 dB under, the bed ducks 7 dB under each line (automation written from the line times, with a short lead and a soft release), and the master is brought to exactly -16 LUFS (a measured gain, not loudnorm, which rides the level). **Titles** (title card, scene titles, name bars, end card) are an animated ASS script burned by libass in the subtitle pass (or one of their own), never a new piece of the timeline, so sync is untouched; layout per format, look per style lock, fonts the unmodified Chillax/Synonym. **Effects** (`shared/finish.py`): grain, vignette and cinema bars are ffmpeg filters (DST-41: a few seconds a scene; grain is encoded under `encode.grain_maxrate_kbps` in `config/video.yaml`), glow and glitch are drawn frame by frame in Python through MoviePy over a scene's finished clip (`Scene.effect`, part of the scene's cache signature); same frame count in and out (verified, and fitted if a codec disagrees); MoviePy is for effects ffmpeg has no recipe for, never for text. Edits: `set_music`, `set_volume`, `set_ambience`, `set_graphics`, `set_effect`, plus `regenerate_shot`, `edit_line`, `set_voice`, `remove_scene`, `move_scene`; steps that follow an op are built by `ops.after_steps`, for clicks and sentences alike. |
| Shot overrides | `shared/schemas/story.py` (`Scene.shot_overrides`), `coverage.apply_overrides`, `PipelineOrchestrator.set_shot`, `EditAgent.set_shot` | **The creator's own camera (DST-38).** A move and/or framing per shot id, laid over the coverage plan by the same code the storyboard and the renderer both call. A close-up needs a face and is otherwise ignored. After a render it is a `shot` job that composes again, re-rendering only the scene whose plan changed. |
| Render progress | `shared/progress_hook.py`, `shared/stand_in.py` | Agents report finished units (`voices`, `pictures`, `shots`, `cut`) through a context variable, which the orchestrator turns into `step` events; a picture drawn by the placeholder after every provider failed gets a `.standin` marker beside it, and the film says which scenes. |
| Camera | `agents/video_agent/camera.py` | Camera moves and the grade over them. The picture is enlarged once with Lanczos and the move is computed at ~3x the output size (both set in `config/video.yaml`, DST-35), then scaled down, which is what makes pans smooth enough to use; moves ease in and out; `move_for` picks it from the shot's job and the scene's tone, `grade_for` from the story's own `visual_style`. |
| Phase 3 | `agents/video_agent/` | `run`: portraits + 3-image shot bank per scene. `compose`: cover each scene (`coverage.plan_coverage`, from the timeline) → render changed scenes only (`plan_signature`) → scene crossfades → master mux → speed → soft-sub tracks. Edits call `compose`. Output is 1080p; pictures stay 1280x720; every quality setting (picture size, upscale filter, easing, sharpen, intermediate vs final CRF/preset) lives in `config/video.yaml` with its reason, read through `shared/video_quality.py`. |
| Phase 5 | `agents/edit_agent/` | `intent_classifier.py` (LLM or regex; matches character names) → `planner.py` (steps) → `executor.py` (reuses Phase 2/3 primitives) → snapshot. Runs as an `edit` job; starts from, and on failure returns to, the saved version's files. `describe.py` says what a request was understood as. Scene-only voices live in `AudioOutput.scene_voices`. |
| Versioning | `state_manager/` | Append-only SQLite log + asset copies per version; `referenced_files(state)` collects every file the state points to. Revert creates a new version. |
| Languages | `shared/languages.py` | Supported subtitle languages (ISO codes + MyMemory codes). UI dropdown is served from here. |
| Tool layer | `mcp/` | Internal tool registry (**not** the MCP protocol). Tools register on `import mcp.tools`; agents call `ToolExecutor().execute("audio.tts", ...)`. Each tool tries providers in order and falls back. |
| Model settings | `config/providers.yaml` + `shared/providers.py` | **Which model serves each role** (story, edit_intent, translate, image, tts, music). Agents never name a model: they call `providers.chain(role)` and use the first available, falling through on failure. `concurrency:` per provider drives `shared/utils/parallel.run_jobs`. |
| LLM client | `mcp/tools/llm_tools/llm_client.py` | `get_llm_client(role)` walks that role's chain: Gemini (google-genai, native JSON schema), Groq / OpenRouter / Ollama / OpenAI (all via the openai client), Anthropic, then `mock` = use the offline fallback. |
| Accounts | `auth/` | `passwords.py` (argon2id), `accounts.py` (users, lockout, project ownership, linked identities), `sessions.py` (opaque token in an HttpOnly cookie, stored hashed), `github.py` (OAuth with GitHub — never joins accounts by email), `deps.py` (**the only place a request becomes a user** — routes depend on `require_user` / `require_project` rather than checking for themselves). |
| Jobs | `jobs/` | **Runs are rows, not closures.** `queue.py` (enqueue/claim/heartbeat/cancel, one atomic UPDATE per claim) and `worker.py` (claims jobs, runs the orchestrator, publishes assets). Cancellation lands between pipeline steps, never mid-ffmpeg. **One job per project at a time, in queue order** — everything that writes a version (plan, render, edit, revert) goes through here. |
| Monitoring | `shared/monitoring.py` + `config/monitoring.yaml` + `backend/request_log.py` | The `monitor` service on the VM: stuck/slow jobs, a dead worker, job and API error rates, disk; one Healthchecks.io check per problem, emailed to the owner. Thresholds carry their reasons in the YAML. API server errors also reach Langfuse as `api-error` events (class and route only). Setup, alert meanings, first steps: `docs/MONITORING.md`. |
| Database | `shared/db.py` | One database for the version log and the queue. SQLite (WAL) by default, Postgres via `DATABASE_URL`, same SQL through SQLAlchemy Core. |
| Assets | `shared/assets.py` | The only place a file path becomes a URL. Local disk by default; `STORAGE_URL=s3://bucket` publishes to any S3-compatible bucket (R2) so the worker and the API need not share a filesystem. |
| Voices | `shared/voices.py` + `backend/routes/voices.py` | Which engines work on this machine and why not, their voices, and a cached one-line sample so a voice can be heard before a render. |
| Fonts | `shared/fonts.py` | Picks an installed font with the script's glyphs (`Segoe UI` on Windows, Noto in the container). A missing font draws nothing rather than failing. |
| Backend | `backend/` | FastAPI routes, DB-backed progress (`services/progress.py`), WebSocket at `/ws/progress/{pid}`, `/assets` serves `data/outputs`. |
| Frontend | `web/` | **The creator interface.** Next.js 16 static export, served by FastAPI at `/` when built. The storyboard is the screen: a plan streams in (script first, then each frame), the creator edits it, renders, and watches with scenes as chapters. See `web/README.md`. |
| Storyboard cards | `agents/orchestrator/storyboard_view.py` | The single source of a card — tone, camera move, lines and the voice that will speak each — used by both the live plan stream and the storyboard endpoint, so a reload shows exactly what the stream did. |
| Classic UI | `frontend/src/` | The original vanilla page, kept at `/classic/` while the new one takes over. |

## Conventions

- Tools return `ToolResult(success, data, error, metadata)`; `safe_run` turns exceptions into failures.
- New tools subclass `mcp.base_tool.BaseTool` and are registered in the category `__init__.py`.
- Every phase writes JSON artifacts to disk and records paths in `state.phaseN.artifact_paths`.
  Snapshots copy every file referenced anywhere in the state (`state_manager.snapshot.referenced_files`),
  so anything an agent or edit records in the state is undoable.
- Clips are rendered to exact frame counts (`-frames:v`), and each clip except the last in a
  crossfade chain gets extra frames (`xfade_frames`) so crossfades never shorten the film.
- Providers must fail loudly: return `success=False` (or raise) rather than silently shipping
  degraded output (e.g. untranslated subtitles, error pages saved as images).
- Test helpers in `tests/conftest.py`: `isolated_dirs`, `small_project` (full 320x180@12fps render
  with silent TTS), `silence_tts(tools)`, `fake_translation`.
- Tests force `LLM_PROVIDER=mock` and `PROVIDER_IMAGE=placeholder` (see `tests/conftest.py`)
  and monkeypatch `shared.constants` paths into `tmp_path`. Point `PROVIDERS_FILE` at a
  temp YAML to test other chains, and call `providers.load(force=True)` after changing env.
- Adding a provider = a `_provider_<name>` method (images) or an adapter in `llm_client`,
  plus an entry in `config/providers.yaml`. Never add an `if os.getenv(...)` chain to an agent.

## Baseline (M0, 2026-09-18)

52/52 tests pass. One CLI run (`--duration 30 --scenes 4`, mock LLM, edge-tts, Pollinations)
took **779 s**: story <1 s, audio 32 s, **images ~630 s (15 serial Pollinations calls, ~42 s each)**,
shot rendering + scene assembly 24 s, final compose ~7 s, subtitle translation 81 s.
Measured voice-vs-picture drift: the voice leads the picture by 2.4 s in scene 1, 3.6 s in scene 2, 5.0 s in scene 3 and 6.1 s in scene 4.

## M1 results (2026-09-19)

87/87 tests pass. Fixed: audio/video/subtitle drift (single timeline; final video frame count
equals the timeline exactly), duration overshoot (template + LLM word budget), untranslated
subtitle tracks (LLM → MyMemory, failed languages skipped), Pollinations downgrade (keyed endpoint
support, image validation, exact size/format, warning when degraded), edit voice bugs (voice kept,
tone/rate/pitch/volume applied via edge-tts prosody, character names understood), edits dropping
multi-language subs and speed, snapshot coverage, silent no-op filters, thread-unsafe progress
events, Windows path bug, UI language list, re-run phase resetting settings.

Real run (same prompt as M0, `--duration 30 --scenes 4 --subtitle-lang Urdu`): 662 s total (images
still ~630 s via the legacy Pollinations endpoint), 30.8 s film (was 60.5 s), final video 739/739
frames = timeline, every line on a cut, Urdu + English tracks. Whisper edit on Aria re-recorded only
her lines (slower -> timeline 34.9 s -> 37.1 s) and the video followed exactly (891/891 frames).
Scene filter edit ~6 s; revert 0.2 s and byte-identical to v1.

## M2 results (2026-09-21)

111/111 tests pass. Added the model settings layer (`config/providers.yaml`): every agent
resolves its model through a role chain with automatic fallback, so swapping in paid
providers later is a config edit. LLM providers: Gemini via the current google-genai SDK
(native structured output) plus Groq, OpenRouter and Ollama through the openai client;
`gemini-flash-latest` is used as the model alias so a retired id can't break it. Images:
Cloudflare Workers AI FLUX (free 10k neurons/day) ahead of Pollinations, with provider
metadata and validation kept from M1. Image and TTS jobs now run in parallel up to each
provider's `concurrency`. Structured-output retries now only repeat for malformed JSON;
a provider that is down is abandoned immediately.

Providers get `retries:` attempts (3 for the free image endpoints) with backoff before the
chain falls through; permanent errors (401/403/400) fail fast. Measured on the live keyless
Pollinations endpoint: 6 images, 2 hit 500s, retries rescued 1 (succeeded on attempt 3) and
1 exhausted its attempts — so retries halved the images lost to placeholders. `python main.py providers`
lists every role's chain and what each alternative needs; `--check` makes one real call per
role to verify keys.

Measured: the keyless Pollinations endpoint 429s on parallel requests (1 per IP confirmed by
experiment), so parallelism only pays off with Cloudflare or a local model. Live M2 run
(24 s, 3 scenes): 397 s, TTS lines 4-up in 5.3 s, 664/664 frames, edits and revert unchanged.
**Not yet verified against live Gemini/Groq/Cloudflare APIs** — no keys on this machine;
adapters are covered by mocked tests.

## M3 results (2026-09-22)

136/136 tests pass. Quality milestone:
- **Mix**: music side-chains to the dialogue (measured ~6 dB duck while a line plays,
  recovering over the next second) so the bed sits louder (0.18 → 0.32); every master is
  normalised to -16 LUFS.
- **Storyboard**: `plan` (script + one preview per scene) → review/edit → `render`, in CLI,
  API and UI, each step snapshotted. Verified end to end in the browser.
- **Character consistency**: `appearance_lock` + fixed `image_seed` per character, used by
  every portrait prompt; "change character design" re-rolls and persists them.
- **Benchmark**: `scripts/benchmark.py` reports per-phase time, length vs target, frame-exact
  sync, lines-on-cuts and which provider served each image; `--offline` runs anywhere.
  Its first real run found the planner's speaking-rate estimate was wrong (films came out
  ~17% long); calibrating WORDS_PER_SECOND 2.6 -> 2.2 against measured edge-tts brought a
  24 s target to 23.5 s (2.1% error). Latest run in docs/BENCHMARK.md.

## Live provider verification (2026-10-01)

Keys arrived; `python main.py providers --check` found four real problems, all now fixed:
- **Reasoning models returned nothing** at small token caps (gemini-flash-latest and
  gpt-oss spend output tokens thinking: 20 tokens -> empty/MAX_TOKENS, 600 -> "OK").
  `MIN_OUTPUT_TOKENS = 600` floor, `reasoning_effort=low` for gpt-oss, and an empty
  response now reports its finish_reason instead of "empty response".
- **Cloudflare flux-1-schnell rejects `seed`** (HTTP 400). The error names the field, so
  the provider drops it and retries once.
- **A Pollinations key with no pollen budget 402s**; it now falls back to the keyless
  endpoint for the rest of the run instead of failing the image.
- **Tests were picking up the real .env** (main.py loads it on import), which would spend
  the owner's quota. conftest sets PIPELINE_SKIP_DOTENV=1 and scrubs every credential;
  scripts/benchmark.py loads .env the same way the app does.

First fully live run (24 s, 3 scenes, Urdu subs): **87.8 s** (was 146 s keyless), script by
Gemini/Groq, 12 of 13 images from Cloudflare FLUX, frame-exact sync, 4/4 lines on cuts,
5.8% length error. In that one run Gemini 503'd and Groq took over, and one prompt was
refused by Cloudflare as NSFW (false positive) and was covered by Pollinations — the
chain degraded visibly instead of silently. See docs/BENCHMARK.md.

## Post-M3 fixes from watching a real film (2026-10-01)

Saad watched the first live render and reported three things:
- **Subtitles never appeared** in VLC or anywhere else. The data was fine (extracting the
  track gave correct Urdu); players simply don't auto-enable mov_text in MP4. Now the
  chosen language is burned in by default (`--no-burn-subs` opts out, `VideoOutput.
  burn_subtitles`), the rest stay as soft tracks minus the burned one, and `.srt`/`.vtt`
  sidecars are written beside the video (VLC auto-loads the .srt; the web player attaches
  the .vtt via `/api/pipeline/subtitles/{pid}`). Non-Latin scripts get a font with the
  glyphs — a Latin-only font drops them silently.
- **Every image looked like anime** regardless of story, from one hardcoded style string.
  `agents/story_agent/visual_style.py` now derives the look: `StoryOutput.visual_style`
  (LLM) > genre preset > default, with the scene tone nudging lighting and the wrong look
  pushed into the negative prompt. `VIDEO_STYLE` overrides everything.
- **Wanted an open-source voice.** Kokoro (Apache-2.0, onnxruntime, no torch, no GPU,
  ~340 MB of model files via `scripts/get_kokoro.py`) is now first in the tts chain when
  `KOKORO_MODEL` is set; ~2 s per line on this laptop. Voice names are per engine
  (`AudioAgent.voice_for`), so "change voice" picks alternates from the right pool.

## M4 results (2026-10-01)

195/195 tests pass. Production architecture:
- **Durable jobs.** A run was a `BackgroundTasks` closure plus a module-level
  dict: a restart orphaned it, nothing outside the process could see it, and it
  couldn't be stopped. Now the API enqueues and returns in milliseconds, a worker
  claims the job with a single atomic UPDATE (Postgres `SKIP LOCKED`; on SQLite
  the `status='queued'` guard makes a second claim match nothing), and progress
  events are rows, so a reconnecting browser replays what it missed. A worker
  that stops heartbeating for two minutes has its job requeued, bounded by
  `max_attempts`. Cancellation is cooperative and lands between steps.
- **One database, two backends.** `shared/db.py` holds the version log and the
  queue through SQLAlchemy Core: SQLite in WAL mode locally (so the API and the
  inline worker can both write), Postgres by setting `DATABASE_URL`. Setting
  `TEST_DATABASE_URL` runs the whole suite against a real server instead of
  throwaway SQLite files, so the Postgres path is tested, not assumed.
- **Scales apart.** `main.py serve` still runs a worker thread, so a laptop
  needs one command; `WORKER_INLINE=0` plus `main.py worker` splits them, and
  `docker compose up --scale worker=3` runs three renders at once.
- **Asset URLs in one place.** `shared/assets.py` replaced three hand-built
  `/assets/...` strings; with `STORAGE_URL` set it publishes to an
  S3-compatible bucket after a job succeeds. **Verified against real
  Cloudflare R2** (2026-10-01): a finished film's 31 referenced assets, 17 MB,
  uploaded through the worker's own publish step, and the film came back
  byte-identical over a presigned URL.
- **Choose and hear the voice.** `/api/voices` says which engines work here and
  why not; `/api/voices/preview` renders a cached one-line sample. The choice
  rides with the run to the audio agent, and a re-run keeps it. A preview that
  fell back to another engine says so rather than passing it off.
- **Portable fonts.** The burned-in subtitle font is resolved against what is
  installed (`fc-match` where available), because `Segoe UI` does not exist in
  the Linux image and a missing font silently draws nothing.

Measured live, API and worker as separate processes:
- A 24 s / 3-scene film with Kokoro voices and burned Urdu subtitles: the POST
  returned at once, the worker finished in **118.8 s**, and the film is correct
  (1280x720, Urdu burned in with right-to-left shaping, English soft track).
- A Kokoro sample for the voice picker rendered in **4.4 s** and is then cached.
- Cancelling a running job stopped it **16.3 s** later at the next step, left no
  error event behind, and the worker went straight on to the next job. (That
  last part needed a fix: `JobCancelled` derives from BaseException, because the
  orchestrator's `except Exception` was catching a deliberate stop and reporting
  it to the browser as a failure.)
- A job queued while the API was **shut down entirely** was still `queued`
  afterwards and ran to completion when the API came back — the exact case the
  old in-memory registry lost.

Verified against a real Postgres 16 (`docker run postgres:16-alpine`):
- The **whole suite, 195/195**, passes with `TEST_DATABASE_URL` pointed at it.
- 40 jobs, 4 separate OS processes claiming at once: 11/11/9/9, **no job
  claimed twice, none left behind**. This is what `SKIP LOCKED` is for.
- The real stack — API plus two worker processes on one Postgres — rendered two
  films concurrently, one on each worker. Wall clock 327 s for both, but they
  were 98 s and 324 s: two ffmpeg pipelines on one laptop fight for cores, so
  extra workers pay off across machines, not on this one.

## Cloudflare account check (2026-10-01)

- `CLOUDFLARE_API_TOKEN` is scoped to **Workers AI only**: `/accounts/{id}` and
  `/accounts/{id}/r2/buckets` both return 403, while the Workers AI endpoints
  answer normally. R2 therefore cannot be reached with the key already in `.env`.
- Workers AI itself is fine, but the **10,000 neuron/day free allocation was
  exhausted** by the M4 verification renders (HTTP 429, "you have used up your
  daily free allocation"). It resets daily; until then images fall through to
  Pollinations' keyless endpoint, which is what the chain is for.
- R2 is now set up (bucket `multi-agent-storygen`). Its S3 access key is
  bucket-scoped, so `ListBuckets` and `HeadBucket` on any other name return
  403 rather than 404 — the bucket name has to be known, it cannot be
  discovered. The `cfat_` management token cannot read the Cloudflare API at
  all (403), which does not matter: only the S3 keys are used.
  `TEST_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
  TEST_S3_BUCKET=multi-agent-storygen python -m pytest -k real_s3` re-proves
  it in one run.
- Testing against a real S3 server found one bug: boto3 defaults to
  virtual-host addressing (`bucket.host`), which needs DNS per bucket and fails
  on R2/MinIO-style endpoints. `shared/assets.py` now uses path addressing
  whenever `S3_ENDPOINT_URL` is set.

## M5 results (2026-10-01)

233/233 tests pass, 37 of them new. Every project used to be visible to anyone
who could reach the API, and `/assets/<project_id>/final_output.mp4` handed over
the finished film to anyone who guessed an id.

- **Sessions, not JWTs.** The cookie carries a random token; the database stores
  only its SHA-256, so a dump can't be replayed as a login. Because a session is
  a row, signing out actually signs out and an admin disabling an account ends
  its sessions immediately — no denylist, no waiting for an expiry.
- **argon2id** via argon2-cffi, with a dummy verify on unknown accounts so the
  login endpoint can't be timed to enumerate who is registered. Wrong password
  and unknown address return the identical 401. Eight failures locks the account
  for 15 minutes.
- **Enforced on the router, not per endpoint.** `dependencies=[Depends(require_user)]`
  is declared where the routers are mounted, so a new endpoint is protected the
  moment it is added. Project-scoped routes take `require_project`, which answers
  **404 rather than 403** — a different status would confirm the id exists.
- **Assets are no longer a static mount.** `/assets/{project_id}/{path}` checks
  ownership, blocks traversal out of the outputs directory, and still answers
  Range requests so seeking in the player works.
- **The WebSocket authenticates too** (the cookie rides the handshake) and closes
  with 1008 for a stranger; progress for any project was previously readable.
- **CORS was `allow_origins=["*"]` with credentials.** Now no cross-origin request
  is allowed unless `CORS_ORIGINS` names one.
- First account is the admin and adopts every project that existed before
  accounts did — including any whose owner was later deleted. Further sign-ups
  need `ALLOW_SIGNUPS=1`; `python main.py users` is the way back in either way.
- The session cookie is `Secure` only when the request arrived over HTTPS;
  marking it Secure on plain HTTP makes the browser drop it, and the sign-in
  appears to work and then doesn't.

Verified in a browser: first-run setup screen, sign-in, the voice preview
working through an authenticated session, sign-out returning to the gate. With
no session, `/api/projects/` and a real film's `/assets/...` URL both answer 401.

## M6 results (2026-10-01)

272/272 tests pass, 39 of them new. Premium video, at $0.

- **Pans work again.** They had been removed with a comment explaining that
  `zoompan` positions its crop window at integer pixels, so a sub-pixel move
  per frame rounds unevenly and the picture shivers. That was true, so it was
  measured rather than argued with — per-frame shift by phase correlation,
  against a 0.000 px floor on a locked-off shot:

      supersample   std dev   worst jump   render (3 s shot)
      1.6x (old)    0.494 px    0.631 px        1.1 s
      3x (now)      0.312 px    0.397 px        2.8 s
      4x            0.242 px    0.365 px        5.1 s

  The move is computed at 3x and scaled down with lanczos, turning a
  whole-pixel error upstream into a fraction of an output pixel. On real shot
  clips from a finished film the new shots travel **up to 5.13 px/frame** — an
  actual pan — while wobbling *less* than the old centred zooms managed at
  0.62 px/frame: worst jump **0.19 px against 0.80 px**.
- **The move means something.** `camera.move_for` picks from the shot's job and
  the scene's tone — tense close-up pushes in, establishing wide pulls back, a
  face delivering a line is locked off — instead of a round-robin over a list.
  The grade follows the story's own `visual_style` (noir, teal-orange, cold,
  warm, bleach) rather than one fixed mild S-curve.
- **A real render found the two modules spoke different tone vocabularies**, so
  "melancholic" and "joyful" both fell through to a generic move. A test now
  asserts every tone `visual_style.TONE_HINTS` can emit has camera moves, and a
  synonym table catches what a writing model invents.
- **Real motion is a provider chain** (`video` and `lipsync` roles) instead of
  an `if os.getenv(...)` ladder inside the agent — which is what the provider
  layer exists to replace. Free providers come first on purpose: a per-clip
  charge does not get to be the default just because it is better. **Veo 3.1**
  is reachable (the Gemini key already present can call it) but is billed per
  second and outside the free tier, so it sits below the free options and
  needs `VIDEO_BUDGET_OK` as well as the key. **Saad ruled it out on cost;
  it has never been called.**

Three robustness fixes found while verifying:
- The worker pool is sized from the *preferred* provider's concurrency, so when
  that one started failing and the chain fell through to an endpoint that takes
  one request at a time, every worker arrived at once and the run came back
  full of placeholders. Each provider now has its own semaphore.
- When every image provider failed, the agent recorded the path it had meant to
  write; the missing file surfaced much later as ffmpeg failing to open its
  input. `_ensure_image` now guarantees a placeholder exists.
- A shot that cannot be supersampled is re-rendered at output size with a
  warning rather than losing the film, and the supersampled frame is capped at
  8.5 MP so a 1080p project cannot exhaust memory.

## M7 results (2026-10-01)

273/273 tests pass. Ship polish:
- **Continuous integration**, which there was none of. `ubuntu-latest` and
  `windows-latest`, Python 3.11 and 3.12, with ffmpeg, the Noto fonts and
  `libgl1` installed on the Linux runner. The suite is fully offline, so CI
  needs no secrets and spends no quota, and `compileall` runs first so a
  syntax error in a file no test imports still fails the build.
  **The first run found a real bug**: on a machine without the optional voice
  packages, choosing Kokoro raised `ModuleNotFoundError: No module named
  'soundfile'` because the import ran before the check that explains what to
  install. It could not show up on the dev laptop, which has them.
- **A film library in the UI.** `/api/projects/` existed and nothing showed
  it, so there was no way back to yesterday's film. Clicking one loads the
  video, its subtitle tracks and its history, or opens the storyboard if it
  was never rendered.
- **A front page that describes the product**, rather than how the original
  prototype's modules were divided.

Worth knowing operationally: `data/state.db` runs in WAL mode, so copying that
file alone does **not** copy recent writes — a backup taken with `cp` came back
empty. Use SQLite's backup API (`sqlite3.Connection.backup`) or `VACUUM INTO`.

## M8 results (2026-10-02)

Frontend rebuilt as the "creator" design — chosen over an editorial dashboard
and an industrial terminal (all three are in `docs/mockups/`), because both
dashboards put the machine in the middle and a content creator doesn't care
that sync is frame-exact. Next.js 16 static export served by FastAPI: still one
container, one origin, no CORS. Title face: Fraunces (eight candidates in
`docs/mockups/fonts.html`).

Verified in a real browser against a copy of the real database: first-run
setup, the library with real posters, a plan streaming in live (script first,
then each frame developing in), a render, and the player — whose scene chapters
seek correctly. The render's log showed all three wide shots
**"reused the storyboard preview"**.

What building it found, all fixed and each pinned by a test that fails on the
old code:
- **Preview reuse never fired in the real path.** `plan()` still defaulted
  previews to 512x288, overriding the 1280x720 the schema said, so signatures
  never matched the render. The earlier test drew the storyboard directly at a
  matching size and passed anyway; the new one goes through `plan` and asks with
  the size `render` actually uses.
- **A scene's tone never reached the camera in a real render** — M6's claim
  was overstated. The agent only makes `establishing`, `character` and
  `lip_sync` shots, and `move_for` sent all three to fixed lists; tone was only
  consulted for a `"detail"` kind the agent never produces, which is exactly
  what M6's test checked. Now establishing shots and narration take the scene's
  mood, and tense dialogue pushes in. The new test renders a film and reads back
  the moves the agent chose.
- **Then every scene opened on the same move.** The pick was offset by scene
  number into each mood's list, and `drift` happened to sit at that position in
  all three — a scene now opens on its mood's own move.
- Scene numbers came from the model's own (zero-based) index; cards and status
  messages now count from one by position.

New backend surface for the UI: the plan streams `storyboard/script` (cards,
cast, image budget, prompt) and `storyboard/frame` events; `GET
/api/pipeline/film/{pid}` gives the player URLs instead of file paths (the old
page split Windows paths in the browser to guess them); the storyboard takes
`?engine=` so cards name the voices that will actually speak; the library has a
poster frame per film; the job status carries the prompt.

CI gained a `web` job (lockfile install, build, typecheck, export check), and
the Dockerfile is now multi-stage so the image serves the new interface — it
would otherwise have shipped the old page, since `web/out` is gitignored. The
Node stage was verified by a clean `npm ci && npm run build` from only the
committed files; the full image has not been built (Docker was not running).

**Voice edits compose.** A scene-scoped voice edit rendered that scene's lines
from throwaway copies of each voice, so the next edit to touch them — "make
everyone louder", a change to one character, "regenerate the audio" — rebuilt
them from the character's voice and the scene's change vanished; asking twice
for a different voice in a scene landed on the same one. Scenes now keep their
own voices (`AudioOutput.scene_voices`) and later edits adjust those as well,
in order: scene 2 whispering, then everyone louder, is scene 2 whispering
louder. "Regenerate the audio" used to rebuild every voice from defaults *and*
switch a Kokoro film to the chain's first engine; it now re-records in place.
The offline classifier missed "voices", "whispered", "deeper", "narrator", so
the natural phrasing of a scene voice edit did nothing; and a tone it didn't
know relabelled the voice, re-recorded identical lines and reported success —
it now fails, naming the tones it knows. Every behaviour test goes through
`EditAgent.edit` on a rendered film, and all six still fail when only the
executor is put back to the old code.

**Edit and undo in the creator interface.** The new UI ended at the player;
editing and versions existed only on `/classic/`. Under the film now: one
sentence, which first comes back as what it was understood to mean, and a
version list in the creator's own words with "Go back".

Building that found the edit path was not safe to put in front of anyone:
- **Edits ran inside the HTTP request** — no progress, no cancel, gone on an
  API restart, and on a worker's host only by luck. Edits and reverts are now
  jobs (`edit`, `revert`); the classic endpoints queue and wait so their
  contract holds.
- **Two jobs for one film could run at once** on two workers, both from the
  same version, the second silently undoing the first. The claim now skips a
  job while an older one for its project is unfinished — decided by that
  row's existence, not its lock, so it holds under `SKIP LOCKED`.
- **A failed edit left its half-applied change on disk** (a filter darkens the
  shots in place, then the cut fails) for the next edit to bake in. An edit
  now starts from, and on failure or cancel returns to, the saved version's
  files.
- **Every revert was saved claiming to be the version it went back to.**
  Restoring a version's files copied its own `state.json` along, and the
  revert's snapshot wrote it over the new record — so the player showed the
  wrong version and "regenerate this scene", which salts its seed with the
  version, could repeat an earlier image.
- **One unreadable image hung a worker forever.** ffmpeg loops a still as an
  endless input and, failing to decode it, retries instead of exiting; the
  heartbeat kept the job looking alive and a cancel never landed. Stills are
  decoded first and shots have a time limit.

Each fix has a test shown to fail without it. `DATA_DIR` now moves everything
a deployment keeps (used to verify against a throwaway instance; the
container will want it too).

**Live, an edit did nothing and said "Change made".** With real keys, Groq
classified "make the voices in scene 2 whispered" as intent `whisper_voices`
with `{"voice_type": "whisper"}` — the prompt asked for "a short snake_case
action name" and free-form parameters, so the model invented both. The
planner fell back to re-recording, the executor ignored the unknown key, and
the same lines were recorded in the same voice. Gemini never got a turn: the
open `parameters` dict became `additionalProperties`, which its developer API
rejects. Now the model fills a closed form (`agents/edit_agent/vocabulary.py`)
whose intents and values are enumerated from what the editor can actually do;
an invented name fails validation and is retried with the error, a request
nothing matches is "unclear", and the planner refuses unclear or incomplete
edits with what to say instead ("which tone? whispered, soft, …"). Measured
against both live models: Gemini now accepts the schema; Groq mapped all five
real phrasings, including "the recipe scene should feel darker" to scene 2 by
its title, and answered "unclear" for "make it better".

Verified end to end in a browser against a throwaway instance (`DATA_DIR`),
on a film made through the interface (Groq script after Gemini 503'd,
Cloudflare FLUX images, Kokoro voices, 107 s render): "make the voices in scene 2
whispered" re-recorded only scene 2 at 140 wpm (its lines 3.8 s → 5.1 s, the
chapters after it moved), "make everyone louder" left scene 2 whispering at
0.91 and everyone else at 1.3, "Go back" to the first cut produced v6 —
recording that it is v6 — with the film byte-identical to v2, and "make it
better" was refused with nothing changed.

**Sign in with GitHub** (`auth/github.py`, an OAuth app, authorization-code
flow; off unless `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` are set):
- A GitHub sign-in **never joins an existing account because the email
  matches.** Addresses here are never proven, so whoever registered the address
  first would receive the sign-in (account pre-hijacking). An account with a
  password connects GitHub from the top bar while signed in instead.
- The callback is refused unless its `state` matches the one handed to this
  browser in an HttpOnly, SameSite cookie scoped to `/api/auth/github` — and
  checked before the code is spent (login CSRF).
- Identities are keyed by GitHub's numeric id, not the login, which can be
  renamed. GitHub-only accounts store an unusable password hash, so there is
  nothing to guess; sign-up policy, the first-admin rule and disabled
  accounts apply exactly as for passwords.
- 15 tests against a fake GitHub; three of them were each shown to fail when
  their guard (the state check, the email refusal, the disabled check) was
  removed. The registered app's keys were checked against GitHub itself: its
  token endpoint answers `bad_verification_code` for them with a dummy code
  and `incorrect_client_credentials` with a wrong secret. The full round trip
  needs a person's GitHub login, so it is the owner's to click through.

## M9 results (2026-10-03) — deployment

Target: Oracle Cloud's Always Free ARM VM, now **2 OCPU / 12 GB** (cut from
4/24 on 2026-06-15). Render speed was measured rather than guessed: a 20 s,
3-scene render took 47.5 s on the laptop's 8 cores and 67.7 s pinned to two
(`start /affinity 5`) — ffmpeg's zoompan is single-threaded, so cores matter
little; slower ARM cores should put a short film at ~3-4 min.

The production image had never been built. Building and running it found:
- **Burned Urdu subtitles were empty boxes in the container.** `fonts.font_for`
  picked "Noto Sans" for every non-Latin language, and on Linux Noto is split
  per script — Noto Sans has no Arabic at all. The tests that existed checked
  the font *name* against a list the wrong name was on (one even asserted
  "Urdu gets Noto Sans"). Fonts are now chosen per language and, where
  fontconfig exists, only if it covers the language — necessary but not
  sufficient: fontconfig lists Noto Nastaliq Urdu, and libass draws it as
  boxes too. `test_m9_subtitle_fonts.py` burns each language through the real
  tool and counts hollow-rectangle glyphs (Urdu in Noto Sans: 19 of 20 shapes
  were boxes; in Naskh, none), and runs inside the image in CI. Windows font
  detection now reads the registry: file names don't follow family names
  ("Nirmala UI" is Nirmala.ttc), so Hindi/CJK were silently skipped.
- **The first boot on a fresh Postgres crashed.** API and worker both created
  the schema at once; the loser died on a duplicate key in Postgres' catalog,
  and only the restart policy hid it. Six processes started at a barrier: 5
  of 6 crashed before, none after — Postgres takes an advisory lock, SQLite
  retries (it raced too, ~1 start in 6, "already exists" / "database is
  locked").
- **Kokoro wasn't in the image** (its packages are optional), and `.env`'s
  Windows model paths would have overridden any container path. The image now
  installs voices and S3 support; compose pins container paths over `.env`,
  and a one-shot `models` service fetches the model into the data volume.
  `get_kokoro.py` used to keep any file over 1 MB — an interrupted download
  included — so it now writes to `.part`, verifies SHA-256, then renames.
- **The worker inherited the image's HTTP health check**, which a worker can
  never pass. It now has its own: a file it touches whenever it reaches the
  queue — one that can't reach the database turns unhealthy.
- **265 MB of the image was never used**: OpenCV and moviepy (with a second
  ffmpeg). Nothing imported either.
- **On a Linux host the containers (uid 10001) couldn't write `data/`.** Docker
  Desktop hides this; the setup script gives them the folder.

Verified in Docker against a fresh Postgres with live providers: first boot
with no restarts and both services healthy; a film planned (21 s), rendered
with Kokoro and burned Urdu (112 s — the Urdu is real text), streamed with
Range requests, and edited ("whispered voices · scene 2"). The whole suite,
**367 tests, passes against a real Postgres 16** — the first time M8's claim
query and `identities` table ran on one.

Backups were proven by a round trip, not assumed: a stack with an account and
a film, two backups (the second hard-links all 17 of its files to the first:
two snapshots, 2.4 MB on disk), the database emptied and the films deleted,
`restore.sh latest` — the account, the version and all 8 files back, owned by
the containers' user, and the restored account signs in and sees its film.

Server pieces (`deploy/`): Caddy for HTTPS (certificates automatic;
`<ip>.sslip.io` works without owning a domain), a prod override where the API
is reachable only through Caddy (so `FORWARDED_ALLOW_IPS=*` is safe and cookies
are Secure), `setup-vm.sh` (Docker, Oracle's iptables, data ownership, `.env`
with a generated DB password) tested in an Ubuntu container with Oracle-style
REJECT rules, `backup.sh`/`restore.sh`, and `deploy/README.md`. CI gained an
`image` job: build, check drivers, run the font and boot-race tests inside
it, start the whole stack on a fresh database and fail on any restart.

**The ARM image builds and runs** (the VM is aarch64; checked under QEMU on
the laptop, `docker buildx build --platform linux/arm64`): every dependency
has an aarch64 wheel (21.6 min emulated), the drivers import, all six
subtitle scripts render, and Kokoro speaks with the real model — its 232 s
for 1.6 s of audio is emulation, not a measurement.

Image size 2.06 GB → 1.68 GB. Suites: **371 passed** on Windows, **367 on a
real Postgres 16**, and the in-image checks pass on both architectures.

**Backups leave the machine** (`scripts/offsite_backup.py`, wired into
`backup.sh` when `.env` names a `BACKUP_BUCKET`): every dump plus a mirror of
the newest films, changed files only — uploads stay single-part below 64 MB so
R2's ETag is the MD5 and an unchanged file is never re-sent (a second push to
real R2: 0 files). Proven as a disaster drill against the real bucket: a stack
backed up, then its database volume, films *and local backups* deleted; a
fresh empty stack and `restore.sh bucket` brought back the account, its
version and all 8 files, and the account signed in and saw its film. `LATEST`
is written last, so a push cut short still names the previous whole backup.

**Live on the VM** (2026-10-03): Oracle Always Free A1 in Dubai, 2 OCPU /
12 GB, Ubuntu 24.04 aarch64, at `https://139-185-59-132.sslip.io`. A VCN made
with the console's plain "Create VCN" has no internet gateway or route, and
then the instance form's public-IP switch won't turn on (the runbook now
starts with the VCN wizard). `setup-vm.sh` worked first time; the native
image build took 4 min 5 s (21.6 min under QEMU); Let's Encrypt issued for
the sslip.io name in ~4 s; first boot on a fresh Postgres with 0 restarts.

Measured rather than estimated: a first real film (44.7 s, 5 scenes, Kokoro,
Urdu burned in, every image from Cloudflare) planned in 18 s and rendered in
398 s, 297 s of it shots. The offline benchmark took 188 s against the
laptop's 47.5 s — an A1 core is ~2.8× slower than a laptop core at this, not
the ~2× guessed — and CPU sampling showed why shots dominate: rendered one at
a time, they held ~130% of 200%, since zoompan is single-threaded. **Shots now
render side by side** across all changed scenes (one per core, at most 4,
`SHOT_WORKERS`): the same benchmark 136 s, shot step 168 s → 116 s, CPU ~196%,
peak memory 471 MB. A second real film after the change (43.3 s, 5 scenes,
18 shots — one more than the first) rendered in **292 s against 398 s**:
shots took 10.8 s each instead of 17.5 s; voices (46 s) and images (25 s)
were unchanged. Of its 19 images 13 came from Cloudflare, 5 reused the
storyboard and 1 went to Pollinations after a Cloudflare NSFW false positive.

Using it found two bugs:
- **The API died once, exit code 1 and no traceback**, as Saad previewed
  voices. "System voices" (pyttsx3) after a Kokoro sample: Kokoro loads a
  bundled espeak into the process, pyttsx3 then started that copy without a
  data path, and espeak answers a missing data file by calling `exit(1)`. A
  render falling back to pyttsx3 in a worker that had used Kokoro would have
  killed the worker the same way. In the image pyttsx3 also had no espeak of
  its own and returned silence as success. Now: pyttsx3 refuses on Linux
  without a system espeak (and the voice picker says why), and loading Kokoro
  sets `ESPEAK_DATA_PATH`. `test_m9_system_voices.py` reproduces it in a
  subprocess with a stand-in Kokoro that starts espeak the same way; run in
  the image, each half of the fix is shown to be needed, and CI runs it there.
- The translation log named the first provider in the chain ("via
  llm:gemini") when Gemini had 503'd and Groq translated.

## DST-38 results (6 Nov 2026): the interface

The creator interface was rebuilt to the design in `docs/UI_DESIGN.md` (Chillax and Synonym, a dark and a light palette, the storyboard as a
sheet, a render screen read from real events) and the friction points behind it were closed: F3, F4, F8, F10, F11, F12, F13, F15, F16, F17,
F22. The back end gained what the screens needed and nothing speculative: step events from the render, a per-shot camera stored on the scene,
a stand-in marker, version numbers the creator can read, and the library's working state. Measured, not claimed: a shot change on a finished
film re-rendered only that scene (2 shots, 19.6 s locally on the offline providers); a whole render of the same three-scene film was 118 s.

## DST-34 results (6 Nov 2026): format, style and the scene count

Create now asks for format and style as well as length and scenes, and each governs the agents instead of being a request the model may ignore.
Measured offline (mock model, placeholder pictures): a plan in 9:16 draws 720x1280 previews and a 1:1 plan 1024x1024; the same three-scene film
renders at exactly 180x320, 240x240 and 320x180 (the small test sizes) with the same frame count as its timeline; a vertical render at the real
1080x1920 took 74 s here. Found on the way: burned-in subtitles were sized for libass's own 384x288 grid, which scales differently in each
direction on a tall film, so the layout is now computed in pixels from the format and converted once (a 16:9 film's subtitles are exactly what they
were); and the scene count was only ever requested in the prompt, never checked. Not done: a real-provider run (no keys here), so the style lock is
proven on the prompts and sizes every provider receives, not on a live Cloudflare or Agnes picture; a finished film cannot change format or style.

## DST-39 results (6 Nov 2026): a wider range of edits (merged to main, PR #29)

One table of editing operations (`agents/edit_agent/ops.py`) now serves a click (`POST /api/edit/{id}/op`), a typed sentence (classifier -> planner -> the same executor step) and the video agent's defaults. Every operation is a validated parameter model, an `apply` that says in words what it did, and the steps that follow it (`ops.after_steps`). Each is a version, "Go back" undoes it, a failed one leaves the film as it was, and the film stays exactly as long as its timeline. Also registered as `edit.<name>` tools (`mcp/tools/edit_tools/`, the static `OP_NAMES` list is pinned equal to `ops.OPS` by a test).

| Area | Operations | Where |
|---|---|---|
| Transitions | `set_transition`, `set_camera` | `shared/transitions.py`, `shared/xfade.py`; picker in `TransitionEditor.tsx`. Length: quick/normal/slow chips or an exact slider, 0.1 s to 1.5 s in 50 ms steps (`ms` on the op; the sentence "a 0.6 s fade" does the same). Defaults: straight cut inside a scene, 400 ms fade between scenes; films made before keep their old 200/400 ms fades (`VideoOutput.legacy_transitions`) and scene cache. |
| Sound | `set_music`, `set_volume`, `set_ambience` | `shared/music.py`, `assets/music/` (20 original CC0 tracks, two per mood, made by `scripts/make_music.py`, `LICENSES.md`), uploads (`POST /api/edit/{id}/music`, rights box, ffprobe checks, 15 MB, 3 s to 10 min, loudnorm), ambience rain/wind/city/sea/night from ffmpeg noise, `SoundPanel.tsx`. Mix gains live on `AudioOutput` (`music_volume` 0.32, `voice_volume` 1.0, `ambience_volume` 0.22 = "100%", multipliers since DST-41 on levels set relative to the dialogue); the master is -16 LUFS, so levels are relative. |
| Titles | `set_graphics` | `shared/graphics.py` builds an animated ASS script burned by libass (title card, scene titles, name bars, end card), layout per format, look per style lock, unmodified Chillax/Synonym in `assets/fonts/` with their licences, `TitlesPanel.tsx`. Overlays only: never a new piece of the timeline. |
| Look | `set_effect` | `shared/finish.py`, **MoviePy 2.2.1** (added with `uv add`; Pillow is now 11.3.0): grain, vignette, glow, cinema bars, glitch drawn frame by frame over a scene's finished clip (`Scene.effect`, part of the scene's cache signature, same frame count in and out), `EffectsPanel.tsx`. MoviePy is for what ffmpeg has no recipe for, never for text (since DST-41 only glow and glitch). |
| Scenes and shots | `regenerate_shot`, `edit_line` (re-records just that line), `set_voice`, `remove_scene`, `move_scene` | executor steps `redraw_shot`, `rerecord_line`, `restructure`; move/remove/redraw controls on the storyboard sheet. |

Sentences understood (offline keyword path in `intent_classifier.py`, closed form for the model in `vocabulary.py`): "slow fade between scenes 1 and 2", "make the music quieter", "play something tense in scene 3", "add rain to scene 1", "add a title card", "add film grain to scene 2", "remove scene 2", "move scene 3 to the start", "redraw shot 2 of scene 3", 'change line 2 of scene 1 to "..."'. The older add/remove background music intents now run `set_music`.

Bugs found by using it, each pinned by a test: (1) a music bed cut from 18 s into its track came out silent because the fades were timed from the seek offset (seek on the input and reset the clock); (2) ambience beds were 20 to 40 dB under the music, night at -53 dB (every kind is level-matched to about -13 dB now); (3) a 24 fps film of three scenes came out one frame longer than its timeline because xfade offsets are floating-point seconds (the join now says how many frames to write); (4) the cancel-an-edit test assumed music was one step (it is choose, mix, cut).

Measured (offline providers, 24 s target, 3 scenes, 7 shots, 28.7 s, 1080p, this laptop): render 55.5 s before DST-39, 51 s and 82 s after (machine noise; scene joins of straight cuts are `concat` copies, ~0.2 s instead of ~4 s). Last full suite 6 Nov: 1136 passed, 5 failed, 7 skipped; the 5 were fixed and re-run (targeted: five `test_dst39_*` files, `test_dst35_video_quality`, `test_m8_edit_jobs`: 216 + 74 passed, then 45 audio and 18 effects tests after the last fixes). Gitleaks clean on the changed files. A live trace on localhost (fresh film, driven through the UI, frame count checked after every step) exercised every operation and "Go back".

After the merge (state on 9 Nov 2026):
- Merged to `main` as PR #29 (`518c99c`). CI's one failure on it was the music-ducking recovery test, which differs by ffmpeg build; it was loosened in DST-39 and made robust in DST-40 (below).
- Still owed: the design canvas does not show the Sound, Titles, Look, scene-change or stacked-change screens (docs/UI_DESIGN.md rule 9 says canvas first; it was skipped for time, and the canvas artifact `6an7trcrooCAGt2UnHfdQy` is now owned by another account, so it must be re-exported from `Desktop/DST-38-evidence/v5-work` before it can be updated).
- An existing film's music or ambience fixes only apply after re-applying any music, ambience or volume edit on it.
- Suggested tickets: human-composed or licensed music (the library is synthesised); a voice-volume sentence ("voices louder" still re-records, the Sound slider changes the mix); the remaining friction backlog (F1, F2, F5, F6, F7, F9, F14, F18, F19, F20, F21), unticketed until Saad says.

## DST-40 results (6 Nov 2026): interface polish (merged to main with DST-39; Jira closed; production updated 9 Nov 2026)

Pure interface work plus the one backend addition it needed. Commits `e5c06c5` (feature) and `02a2962` (test fix). Saad merged it after DST-39, closed both tickets in Jira and updated the live site (told to Claude on 9 Nov; the lines below that say "not merged" or "pending" were written on 6 Nov and are history).

- **Create** (`Composer.tsx`, `PromptSettings.tsx`): the prompt is one line that grows to five (about 136 px, was about 380); format, style, length and scenes sit behind a "+" in a native `<dialog>` window (top layer, so nothing can paint over it; focus trapped and returned; Escape, scrim, X and Done close it; fades in over 0.22 s, none under reduced motion). The chosen values are said beside the "+". Enter sends. The hint box is gone; its cost sentence is the window's footer. Length and scenes show the choice by outline only (no tick), a recorded departure from rule 4.
- **Watch** (`studio/Inspector.tsx`): Change, Sound, Look, Titles and Versions are tabs in a sticky panel beside the film (from 1100 px), so a change is made and watched without scrolling.
- **Review** (`studio/Sheet.tsx`, `TransitionEditor.tsx`): small shot cards with two tags (camera move, transition) that open the matching tab of the same panel; the panel holds Camera and Transition for a shot, Words and picture and Order for a scene. Transition styles are a compact grid so Speed and the Exact length slider stay in view. On a phone the panel is a bottom sheet. A change from the board no longer throws the creator out to the film.
- **Stacked changes** (`studio/staged.ts`, `ChangeBar.tsx`, `Studio.tsx`; backend `ops.check_many`, `ops.after_steps_many`, `EditAgent.apply_ops`, `PipelineOrchestrator.apply_ops`, job kind `ops`, `POST /api/edit/{id}/ops/check` and `POST /api/edit/{id}/ops`): on a finished film a click adds to a stack instead of starting the slow re-cut. A bar bottom left shows "N changes waiting" (list, Discard, Apply); stacked changes are shown on screen as made and marked "Waiting"; two choices about the same thing become one; each is checked against the film and the stack when chosen; Apply is one job and one version, the sound mixed and the film cut once. Typed changes are stacked too and applied after, each as its own version. Before a render everything still applies at once. Leaving with unapplied changes asks first.
- **A stale server** answers the new endpoints with 405 ("Method Not Allowed"); the page now says to restart the server (`lib/api.ts`). Restart `python main.py serve` after pulling.
- **Found by using it:** the settings panel was painted over by the film list (a hand-placed drop-down; replaced by the native dialog); the music-ducking CI test (`tests/unit/test_m3_audio_mix.py`) failed twice on CI because recovery after a spoken line depends on the ffmpeg build (4.3 dB at 3 s here, about 1 dB on the runner): it now builds a 9 s bed and judges recovery at 6 to 7.5 s, and checks it did not fall again. That fix (`02a2962`) is **not yet confirmed green on CI**.
- **Tests:** `tests/unit/test_dst40_batch.py`, 5 passed in the project venv (the conda `(base)` Python lacks argon2 and fails the API test: always use `.\.venv\Scripts\python.exe`). Browser runs on localhost covered the prompt window, the panel, stacking and Apply, in dark and light and at 390 px. Gitleaks: no leaks (109 commits). The full suite has not been run on this branch.
- **Not tested:** applying a stack that includes a typed change; a stack that fails part-way (it should stay for a retry); scene Order actions and "Draw this shot again" in a browser.
- **Known limits:** the design canvas does not show these screens; "Redraw it" on the stand-in note now stacks instead of starting; typed changes are understood only when applied.

**Handoff state (read this first if you are a new session):**
- Saad runs all git. On 9 Nov DST-39 and DST-40 were both merged, closed in Jira, and the production site was updated. Whether a separate ticket for further UI polish was created is not known to Claude.
- **QA of 9 Nov 2026** (localhost, white and black box): report `Desktop/Dastango-QA-report-9-Nov-2026.md`, evidence in `Desktop/Dastango-QA-evidence-9-Nov-2026/`. Items 1 to 9 of its ticket were fixed in DST-41 (next section); the audio ceiling is DST-42 and the polish items are DST-43 (results below).
- A one-page sprint report for 2 to 6 Nov is at `Desktop/Dastango-sprint-report-2-6-Nov-2026.pdf` (generated with reportlab, not opened to check the layout).

## DST-41 results (10 Nov 2026): fixes from the localhost QA (branch `DST-41`)

Ticket items 1 to 9 of the QA (`Desktop/Dastango-QA-report-9-Nov-2026.md`); items 10 to 15 are DST-42 (audio above 22.05 kHz mono, ambience in the speech band) and DST-43 (transitions, titles, API, typed sentences, small screens).

- **The mix is set from the dialogue** (`mcp/tools/audio_tools/audio_merger.py`, constants at its top). Every line is brought to -20 LUFS (`_level`, at most 12 dB either way; a whisper or shout keeps its own offset, `AudioAgent._tone_offset_db` from the voice config), the music bed to -35 LUFS and the ambience to -40 LUFS at "100%" (`_bed_gain`, measured, so a loud file is not a loud bed), the bed dips 7 dB under each line (`duck_expression`: automation written from the line times, 0.12 s before, 0.25 s to full depth, 0.5 s back; lines closer than 0.7 s are one dip), and the master is brought to exactly -16 LUFS by a measured gain plus a limiter (`_master`). ffmpeg's `sidechaincompress` was tried and dropped (its depth followed the key's wave, not the line) and so was `loudnorm` (its dynamic fallback lifts the bed in the gaps). Measured on a real Kokoro film: speech over the bed under each line 5.1 to 7.7 dB before, **17.2 to 20.2 dB** after (17.2 to 19.7 with rain), about 11 dB between lines, final -16.1 LUFS (was -16.5 to -17.0). The limiter's 5 ms look-ahead had delayed the whole mix by 109 samples: padded and trimmed (`test_the_master_does_not_shift_the_voices_in_time`). `music_volume` 0.32, `ambience_volume` 0.22 and `voice_volume` 1.0 are now multipliers on those levels.
- **Effects**: grain, vignette and cinema bars are ffmpeg filters (`shared/finish.py` `_apply_ffmpeg`), glow and glitch stay in MoviePy. Grain is uniform temporal noise (`GRAIN_STRENGTH` 9, seeded) encoded under `encode.grain_maxrate_kbps` (8000) in `config/video.yaml`. Measured on a real 6.4 s 1080p scene (3.2 MB plain): grain soft / normal / strong 3.6 / 6.1 / 6.8 MB in 2 to 4 s each (was 7 to 18 times the plain file in 27 to 64 s); on a real 23 s film (14.4 MB): 12.9 / 15.6 / 22.8 MB; glow takes 19 s a 6 s scene. The Look panel says which looks are quick. A rate-limited x264 encode is not bit-for-bit repeatable between runs; the grain is (seeded).
- **Stop stops** (`shared/progress_hook.py` `pulse`, `jobs/worker.py` `check_cancelled`): edits run inside `_following_steps(editing=True)`, so every reported unit (a shot, a look, the cut) is a point where a cancel lands, MoviePy's frame loop pulses once a second, every edit ends with a `save` step before its snapshot (a cancel can no longer arrive after the version is written), and `run_jobs` no longer lets queued shots run on after a cancel. A cancelled edit saves nothing; Stop lands within the longest shot already running (21 s on the QA film, was 29 to 38 s). The page shows **Cancelling** at once (`Studio.tsx` `cancelling`), and the server writes a `cancelling` event when Stop is pressed, so a reload or a second tab shows it too.
- **Smaller**: the changes bar no longer covers the film's controls (`.barUp`, the film stops 360 px above the window's bottom while changes wait); the 9:16 preview in the Review panel is at most 220 px tall; `edit_intent` models give up after 10 s; `POST /api/pipeline/render/{id}` answers 409 while an `op`, `ops`, `edit`, `shot`, `revert`, `respec` or `render` job is unfinished (a typed sentence still queues behind a stack: the bar relies on it); no fade at a scene join, and beds are cut in the filter chain (an MP3 seek left 43 to 68 ms of silence at the start of every bed, the real cause of the hole at each cut) with the limiter's 5 ms delay trimmed from ambience.
- **Test hygiene**: `tests/conftest.py` imports MoviePy before the scrub (its `load_dotenv` had put a developer's whole `.env` into the process), so the full suite is the same on a machine with a `.env`. Measuring a slice with `ffmpeg -i f -ss a -to b -af volumedetect` is wrong on this ffmpeg (the output-side `-ss` is applied after the filter, so it averages the whole file): cut inside the filter (`atrim`), as `test_m3_audio_mix.py` now does; that is the likely cause of the music-ducking test's flakes on CI.
- **Not done / limits**: Stop cannot interrupt an ffmpeg already running; a Stop during the very first seconds of a render was not seen showing "Cancelling" in the browser (that render stopped within a second); `Look` still takes minutes on a long film with glow; the audio chain was still 22.05 kHz mono (fixed in DST-42, below).

## DST-43 results (10 Nov 2026): polish from the localhost QA, items L1 to L5 (branch `DST-43`)

- **Typed sentences (L4)** (`agents/edit_agent/intent_classifier.py`, `agent.py`): "turn the music off", "music off", "switch the music off in scene 2", "kill the music", "play something tense in scene 3" (the example in this file now works), "scene two should feel more tense" (a scene's mood is its music), "turn the music down/up", "make it look like old film" (film grain: one effect per scene, so grain and not a stack) and number words after scene/shot/line ("scene two") are understood offline. **A sentence of several requests** ("add rain and make the music quieter") is split where a second request starts (`split_request`: after "and", "then", a comma or a semicolon, and only when a verb follows, so "scenes 1 and 2", "the music and the rain" and quoted text stay whole); the parts that are shared operations are applied together as one version (`EditAgent._edit_several`, the same `apply_ops` a stack uses) and every part that was not done is named in `EditResult.not_done` and in the completion event ("Change made. Not done: ..."), so half a sentence is never dropped silently. Limits: a scene named in one part is not carried to the others; "make the music and the rain quieter" is still read as the music only.
- **API (L3)** (`ops.BadValue`, `backend/routes/edit.py`, `history.py`): a value that is wrong whatever the film is (an unknown mood, transition, effect, ambience, strength or tone, a length outside 100 to 1500 ms, a level past the top) answers **400**; only what depends on the film (no such scene or shot) stays **404**. `POST /api/history/{id}/revert/{version}` answers **409** when the film is rendered and that version is a storyboard draft with no film. The "24 of 50 routes missing from `docs/API.md`" in the QA report was a loose text match: all 51 OpenAPI routes are listed, and `test_dst43_api.py` now pins the docs and the routes against each other both ways.
- **Titles (L2)** (`shared/graphics.py`): the title card ends when the first burned subtitle begins (never under 0.9 s; `subtitles=` on `build_ass`); in the one pass the titles are drawn **under** the subtitles (`subtitle_tool`), so the end card's scrim no longer dims the last line; the title, rule and tagline are laid out from measured lines (Pillow on the two faces), so a title of two or three lines in 9:16 pushes the rule and the tagline down instead of being crossed; the rule is centred (it sat half its width to the left); `…`, an em dash and curly quotes keep the chosen face (only a letter of another script, `_other_script`, switches it); `{...}` blocks and the ASS escapes for a line break or a space are removed as codes, not left as "fs250c&H0000FF&" or a stray "N". Checked on rendered frames in 16:9 and 9:16.
- **Transitions (L1)** (`shared/xfade.py`, `shared/transitions.py`, `storyboard_view.cap_transitions`, `TransitionEditor.tsx`): the **zoom** is no longer ffmpeg's `zoomin` (it magnifies the last shot into a blur and a flat colour) but the overlap frames pushed in by a scale animation and joined by a plain fade (`_zoom_join`: the overlap is cut out by frame number, so the film's length stays exact). Measured on 1080p with 19 overlap frames: 1.5 s against 1.85 s for `zoomin`, 77 frames out of 77. A custom `xfade` expression was tried first and took 43 s for the same frames. A length longer than half the shorter shot is held to that (it always was: a 1.2 s zoom became 0.75 s); every storyboard card's `transition` now carries `max_ms` and `capped`, from the plan's estimates or, on a cut film, the real shot lengths, and the picker says "Held to 0.75 s here" instead of staying silent.
- **Small screens and accessibility (L5)**: the Watch panel stands beside the film from 900 px (it waited for 1100: at 1024 x 768 it was 870 px down the page); checked at 1024, 900 and 899. The `aria-label` on three plain divs became `role="group"`; Films goes h1 to h2 (cards take a `heading` level, the home page keeps h3 under "Your films"); the appearance choice in the account menu is a radio group with one tab stop and arrow keys. axe: 0 violations on Films and on Watch at 1024.

## DST-42 results (10 Nov 2026): the audio chain above 22.05 kHz mono, ambience clear of speech (branch `DST-42`)

- **The chain is 44.1 kHz stereo** (`shared/music.py` `SAMPLE_RATE`, `CHANNELS`; `audio_merger.py`, `bgm_tool.py` and `AudioOutput`/`TimingManifest` follow it). Voices are still placed mono (Kokoro and Edge speak at 24 kHz, so they cannot gain bandwidth; they are resampled once) and are put in both ears with `pan` (an `aformat` upmix takes 3 dB off each ear: the first version of this did, and the speech over the bed measured 3 dB lower than before until it was found); the music and ambience beds, the premaster and the master are stereo, and the finished film's AAC track is 44.1 kHz stereo (it was 22.05 kHz). Loudness is set per channel (`per_channel_lufs`: BS.1770 adds the powers of two channels, so a stereo file measures 3 LU over one channel), which keeps every line exactly as far above its bed as DST-41 set it: measured on the real Kokoro film, speech over the bed 19.0 to 21.2 dB (no ambience) and 18.7 to 20.8 dB (rain) after, against 19.0 to 21.3 and 18.5 to 20.7 before, by the same method on the same lines; master -16.0 LUFS. Audio files are twice as large as before (stereo at twice the rate: about 10 MB a minute for the master as WAV) and snapshots copy them.
- **Uploads** keep their channels: a stereo track is stereo MP3 at 44.1 kHz and 192 kbps, a mono one 128 kbps (they were folded to mono at 32 kHz, 96 kbps); still normalised to -16 LUFS. The library (20 synthesised tracks) is still 32 kHz mono, so it plays in both ears at the same level and has no content above 16 kHz; recorded or licensed music is a separate ticket (see the DST-42 ticket's out of scope).
- **Ambience** (`shared/music.py` `AMBIENCE`, `ambience_bed`): the five kinds are level-matched by loudness, not by ear: measured with ebur128 on 4, 6, 10 and 12 s beds they are within 1.5 LU of each other (they were 4.1 LU apart: rain -10.4, night -10.0, wind -13.4, city -14.1, sea -12.2; now -22.0 for every kind at ten seconds), `AMBIENCE_LUFS` -22. Their energy is out of the speech band: the share of energy between 2 and 5 kHz was rain 36%, night 63%, sea 8%, now 5.6%, 3.5%, 2.5% (rain has a cut at 3.3 kHz and keeps its hiss above it, night is insects from 6.5 to 14 kHz, the sea is lower). Each bed is two noise sources with fixed seeds, one per ear, so wind and sea are wide and a bed is the same every time. A scene's bed is taken from the film's own clock (`offset_ms`): two scenes of one ambience are one continuous sound, with the swell carrying across the cut (each scene used to start its swell again).
- **Not done / limits**: the synthesised music library is unchanged; the ambience is still filtered noise, not recordings (a recorded rain is a library question); the TTS engines' own 24 kHz is the voice's limit; old films keep their 22.05 kHz audio until an edit that mixes the sound (any music, ambience or volume edit) rebuilds it.

## Known issues / next milestones

- The Pollinations key now has a small pollen budget, so the keyed endpoint
  serves the requested model at full size. The keyless endpoint it falls back
  to when the budget runs out 402s intermittently and returns a weaker model
  (`sana`, ~1024x576), so a run can degrade mid-film without failing.
- All three free image providers can be exhausted at once — Cloudflare's 10,000
  neurons/day, Pollinations keyed and keyless — and then a render completes
  with placeholder images rather than failing. That is the designed behaviour,
  but it does mean a demo render has to wait for the daily reset.
- No real-motion provider has been exercised end to end: every one costs money
  per clip. The chain, the Veo adapter and the fallback to camera moves are
  unit-tested; the network call is not.
- Cloudflare's safety filter occasionally rejects an innocuous scene prompt as NSFW.
- Kokoro is the default voice and needs no torch (onnxruntime, ~340 MB of model
  files via scripts/get_kokoro.py); edge-tts, gTTS and pyttsx3 are the fallbacks
  and the UI can pick between them. Still deferred: Chatterbox voices and
  ACE-Step music, which do need a ~2-3 GB torch install.
- Snapshots copy every file per version — storage grows quickly (content-addressed storage would fix it).
- The S3/R2 asset backend is verified against both a local S3 server
  (`python -m moto.server`) and **real Cloudflare R2**, bucket
  `multi-agent-storygen`: 31 assets / 17 MB up, film back byte-identical.
  **Local disk is the default and stays the default** — the API and the
  worker share a disk here, so a bucket would only add latency and one-hour
  presigned URLs. Only `STORAGE_URL` switches it; having R2 credentials in the
  environment does not, and a test pins that down.
- Two workers on one laptop contend for CPU; concurrency helps across hosts.
- No password reset by email (no mail service at $0) — `python main.py users
  passwd <email>` is the recovery path, which suits a self-hosted deployment.
- No two-factor; sessions are not yet listed and revokable per device in the
  UI, though the API and the data model both support it.
- GitHub can be connected but not disconnected from the UI yet (an account
  made through GitHub has no password, so disconnecting needs a set-password
  step first). GitHub's callback must match a registered redirect URI
  exactly: open the app at `http://localhost:8000`, not `127.0.0.1`, or pin
  `GITHUB_CALLBACK_URL`. One OAuth app takes up to ten redirect URIs, so the
  laptop and the server share it.
- Off-machine backups mirror only the newest films; older database dumps are
  kept, but restoring an old one pairs it with today's films. R2's free 10 GB
  will eventually be outgrown by films + versions (snapshot storage growth,
  below).
- Docker Desktop on Windows reaches bind-mounted `data/` through a slow
  file-sharing layer, so container timings on the laptop (a scene-2 voice
  edit took 100 s) say little about a Linux host's own disk. The VM's
  numbers are in M9.
- Renders on the free VM are CPU-bound (~2.8× slower per core than the
  laptop). The remaining lever is `SUPERSAMPLE` 3 → 2, which trades some pan
  smoothness for speed; not taken.
- Kokoro's first line in a fresh worker loads its model (~30 s on the VM).
- An edit that rewrites the script starts its voices fresh (scene 2 is a
  different scene afterwards); only the voice engine carries over.
- Versions saved by a revert before M8 carry the wrong `version` inside their
  state (the number they went back to). History order is unaffected and the
  next save numbers correctly; nothing rewrites the old records.
