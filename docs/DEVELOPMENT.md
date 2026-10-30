# Development

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| Python 3.11 or 3.12 | Everything except the UI | python.org |
| uv | Installs the exact locked Python packages | `pip install uv` · or [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |
| FFmpeg on `PATH` | All audio and video work | Windows `winget install Gyan.FFmpeg` · macOS `brew install ffmpeg` · Debian/Ubuntu `apt install ffmpeg` |
| Node 22 | Only to build or develop the UI | nodejs.org |
| Docker | Only for the container stack | docker.com |

## Setup

```bash
git clone https://github.com/saad-dafinitiq/Project-Dastango.git
cd Project-Dastango
uv sync                                    # creates .venv with the exact locked versions
source .venv/bin/activate                  # Windows: .venv\Scripts\activate
uv sync --extra voices                     # optional: Kokoro voices
python scripts/get_kokoro.py               # optional: the voice model (≈340 MB); prints two lines for .env
cp .env.example .env                                        # every key is optional
cd web && npm ci && npm run build && cd ..                  # optional: the creator UI
```

With no keys at all, everything runs offline: a template script, keyword
edits, placeholder images, and Kokoro or online voices. [PROVIDERS.md](PROVIDERS.md)
lists the free keys worth adding.

## Dependencies

Python packages are declared once, in `pyproject.toml`, and pinned to exact
versions in `uv.lock`. Both are committed. Your laptop, CI and the Docker image
all install from the lock, so they run the same version of every package,
including the ones nobody named (F11 in [REQUEST_TRACE.md](REQUEST_TRACE.md)).

| To | Run |
|---|---|
| Install what the app and the tests need | `uv sync` |
| Add an optional part: `postgres`, `s3`, `voices` or `tracing` | `uv sync --extra voices`, several together as `uv sync --extra voices --extra s3`, or everything with `uv sync --all-extras` |
| Add a dependency | `uv add <package>` (`uv add --optional voices <package>` for an extra, `uv add --dev <package>` for the tests), then commit `pyproject.toml` and `uv.lock` together |
| Upgrade one package | `uv lock --upgrade-package <package>`, then `uv sync` and run the suite |
| Upgrade everything | `uv lock --upgrade`, read the diff in `uv.lock`, run the suite |
| Check the lock is current | `uv lock --check` (CI and the image build fail when it is not) |

`uv sync` makes `.venv` match the lock exactly, so it **removes** extras you do
not name: list every one you use each time, or use `--all-extras`. A plain pip
list of the pinned versions, if a tool needs one, is `uv export --no-hashes`.

### Local Stable Diffusion (optional, NVIDIA GPU)

Set `LOCAL_SD=1` to draw images in-process with diffusers. It pulls PyTorch, so
it is **not** in `pyproject.toml` or `uv.lock`: the lock would pin PyPI's torch,
not the CUDA build your GPU needs, and would add about 45 packages for everyone.
Install CUDA torch first (for RTX 30/40-series), then the rest:

```bash
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
uv pip install "diffusers>=0.27" "transformers>=4.40" "accelerate>=0.30" "safetensors>=0.4"
```

These packages are unpinned, and a later plain `uv sync` removes them: use
`uv sync --inexact` to keep what is already installed.

## Running it

```bash
python main.py serve                 # UI + API + a worker thread on http://localhost:8000
cd web && npm run dev                # UI with hot reload on :3000, API proxied to :8000
WORKER_INLINE=0 python main.py serve # API only…
python main.py worker                # …and workers as their own processes
docker compose up --build --scale worker=3   # API, three workers and Postgres
```

To try the container stack without touching your real films or port 8000:

```bash
DATA_PATH=/tmp/storygen-try HOST_PORT=8090 docker compose -p storygen-try up --build
```

### The CLI

Every operation the UI has is scriptable.

| Command | |
|---|---|
| `python main.py "prompt" --duration 30 --scenes 4` | Plan and render in one go |
| `python main.py plan "prompt" --scenes 4` | Storyboard only |
| `python main.py storyboard <id>` | Show it |
| `python main.py restyle <id> scene_2 --visual "…"` | Edit a scene before rendering |
| `python main.py render <id>` | Render an approved storyboard |
| `python main.py edit <id>` | Interactive edit prompt: `edit> make scene 2 darker` |
| `python main.py history <id>` · `list` | Versions; all projects |
| `python main.py jobs [--status failed]` | The queue: what ran, what broke, why |
| `python main.py providers [--check]` | Which model serves each role; `--check` makes one real call per role |
| `python main.py users [create <email> --admin \| passwd <email> \| role <email> admin]` | Accounts |
| `python scripts/benchmark.py --offline` | Fixed prompts, measured |
| `python scripts/benchmark.py --testset eval/testset.jsonl --render-subset` | The labelled test set: plan each prompt, fully render the 8 marked ones (see `eval/README.md`) |

Outputs go to `data/outputs/<project_id>/`, version snapshots to
`data/state_versions/`, and the SQLite database to `data/state.db`.
`DATA_DIR=/elsewhere` moves all three.

## Tests

```bash
python -m pytest -q        # 388 tests, offline, ~6 minutes
```

See [TESTING.md](TESTING.md) for running against Postgres, S3 or inside the
production image, and for what the suite covers.

## Repository layout

```
agents/
  orchestrator/     plan · render · run_full · edit · revert as small graphs
  story_agent/      prompt → ScriptOutput; template fallback; appearance locks; visual style
  audio_agent/      voices → timeline → music → master
  video_agent/      images → camera moves (camera.py) → shots (animator.py) → film
  edit_agent/       vocabulary · classifier · planner · executor · describe
mcp/                tool registry and tools (llm, audio, vision, video, system)
shared/
  schemas/          Pydantic contracts: PipelineState and every phase's output
  timeline.py       the single source of timing
  providers.py      role → provider chain, from config/providers.yaml
  db.py             SQLAlchemy Core: version log, queue, accounts
  assets.py · fonts.py · languages.py · voices.py
state_manager/      append-only versions, file snapshots, revert
jobs/               queue.py (claim, heartbeat, cancel) · worker.py
auth/               passwords · sessions · accounts · github · deps
backend/            FastAPI app, routes, progress WebSocket
web/                Next.js creator interface (static export), including the /docs guide
frontend/           the original single-page UI, still served at /classic/
config/             providers.yaml
deploy/             Caddyfile, prod compose override, setup, backup and restore scripts
scripts/            benchmark, Kokoro download, off-machine backup
tests/              unit/ and integration/
docs/               this documentation
```

## Conventions

- **Agents never name a model.** Ask `providers.chain(role)`; add providers in
  `config/providers.yaml`.
- **Tools return `ToolResult(success, data, error, metadata)`**, and record in
  `metadata` which provider actually served the call. `safe_run` turns
  exceptions into failures.
- **Fail loudly.** Return `success=False` or raise rather than ship degraded
  output under a success label.
- **Timing comes from `shared/timeline.py`.** Never time audio or video
  independently.
- **Anything recorded in the state is undoable**, because snapshots copy every
  file the state refers to (`state_manager.snapshot.referenced_files`).
- **Clips are rendered to exact frame counts** (`-frames:v`); every clip in a
  crossfade chain but the last gets extra frames for its crossfade.
- **Requests become users only in `auth/deps.py`; paths become URLs only in
  `shared/assets.py`.**
- **Tests go through the real entry point** (`plan`, `render`,
  `EditAgent.edit`, the HTTP API) and are shown to fail without the fix.

## Extending it

**A new edit.** Add it to `EDITS` in `agents/edit_agent/vocabulary.py` (its
target, what it needs, what it means; the model's form is generated from
this), a plan in `planner.py`, a step in `executor.py` built from existing
agent primitives, words for it in `describe.py`, and a test through
`EditAgent.edit` on a rendered film.

**A new provider.** See [PROVIDERS.md](PROVIDERS.md#adding-a-provider).

**A new tool.** Subclass `mcp.base_tool.BaseTool`, give it a `name` such as
`video.something`, and register it in its category's `__init__.py`.

**A schema change.** `shared/schemas/` is the contract between every phase and
every stored version. Add fields with defaults so old versions still load.
