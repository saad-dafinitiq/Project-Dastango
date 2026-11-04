# Testing and quality

**388 tests**, fully offline, on Linux and Windows in CI, plus the checks
that only mean something inside the production image, run inside it.

## How the suite is built

**Offline by construction.** `tests/conftest.py` forces `LLM_PROVIDER=mock`
(the deterministic template writes scripts, the keyword classifier reads
edits) and `PROVIDER_IMAGE=placeholder`, renders voices as silence of the
right length, points every data path at a temporary directory, sets
`PIPELINE_SKIP_DOTENV=1` and scrubs every credential. No test can read the
real `.env` or spend real quota, and CI needs no secrets.

**Through the real path.** Behaviour tests go through `plan`, `render`,
`EditAgent.edit` and the HTTP API (the same calls the product makes) and
render real (small: 320×180 at 12 fps) films with ffmpeg. Twice a unit test
passed on a code path nothing used: preview reuse was tested by drawing the
storyboard directly at a matching size, while the real `plan` drew it at
another; scene tone was tested on a shot kind the agent never produces. Both
tests were rewritten to go through the entry point, and both found the bug.

**Proven to fail without the fix.** A bug fix lands with a test, and the test
is run against the old code to show it fails for the right reason. Examples
from the last milestone:

| Test | On the old code |
|---|---|
| Two shots render at once | `assert 1 == 2`: one shot at a time |
| A Kokoro voice, then System voices, leaves the process alive | the subprocess exits with code 1: `Error processing file '…/phontab'` |
| Translation names the provider that answered | `llm:gemini` instead of `llm:groq` |
| Six processes boot against one fresh database | 5 of 6 crash creating the schema |
| Urdu burns as real text | 19 of 20 glyph shapes are hollow boxes |

## Running it

```bash
python -m pytest -q                                   # everything, ~6 min on a laptop (what Gate 1 requires)
python -m pytest -q -m "not slow"                     # what a pull request runs: everything but the SLOW list
TEST_SHARD=2/2 python -m pytest -q -m "not slow"      # one pull-request CI job: slice 2 of 2
python -m pytest tests/unit/test_m8_edit_jobs.py -q   # one area

# The same suite against a real Postgres
docker run -d --name storygen-pg -e POSTGRES_USER=storygen -e POSTGRES_PASSWORD=storygen \
  -e POSTGRES_DB=storygen -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+psycopg://storygen:storygen@localhost:55432/storygen python -m pytest -q
# (run it as one process: the tests share the one server and empty its tables, so two
# slices must not run at once against the same server)

# Object storage against a real S3 server (or R2 with real credentials)
python -m moto.server -p 5111 &
TEST_S3_ENDPOINT=http://127.0.0.1:5111 python -m pytest -k real_s3

# Inside the production image (fonts, drivers, voice libraries)
docker run --rm -e PIPELINE_SKIP_DOTENV=1 storygen:local python -m pytest -q \
  tests/unit/test_m9_subtitle_fonts.py tests/unit/test_m9_schema_race.py tests/unit/test_m9_system_voices.py
```

## What is covered

| Area | Tests | Highlights |
|---|---|---|
| Script and storyboard | 35 | Template length within 15% of target; every speaker is in the cast; appearance locks persist; the plan streams the script before any frame; previews are reused by the render |
| Voices and sound | 40 | Ducking and loudness; engine availability and its reasons; scene-only voice overrides that survive later edits; the espeak crash reproduced in a subprocess |
| Picture, sync and subtitles | 105 | Final frame count equals the timeline; every line starts on a cut; camera moves follow shot and tone; pan smoothness; subtitle fonts burn every script without missing-glyph boxes; shots render in parallel and still cut each scene from its own shots |
| Editing and versions | 71 | 18 phrasings classified offline, plus scope and parameter extraction; closed vocabulary rejects invented intents; edits as jobs, one per film at a time; a failed edit restores the saved files; revert is byte-identical and saved as a new version |
| Providers | 21 | Chains, credential filtering, fall-through, retries, mock fallback, the answering provider is the one reported |
| API, jobs and accounts | 86 | Atomic claims, heartbeats, requeue, cancellation; sessions, lockout, timing-uniform login, 404 for others' projects, asset traversal; GitHub OAuth against a fake GitHub (state, email refusal, disabled accounts) |
| Deployment and storage | 29 | Local and S3 assets; worker liveness; Kokoro download verified by SHA-256; first-boot schema race; off-machine backup push and pull against moto |
| End to end | 1 | Prompt to MP4 in mock mode |

## Continuous integration

Two workflows. What gates a pull request is kept small and fast; the wide matrix
and the heaviest tests run every week, so coverage is kept without every merge
paying for it. The reason is the free GitHub plan's monthly minutes (below).

**On every pull request** (`.github/workflows/tests.yml`):

| Job | What |
|---|---|
| `ubuntu-latest · python 3.11 · slice 1 … 2 of 2` | `compileall` (a syntax error in a file no test imports still fails), then the tests that are not marked `slow` (`-m "not slow"`) with ffmpeg and the Noto fonts, in two slices on two machines. Python 3.11 is what the production image runs |
| `web · build and typecheck` | `npm ci` from the lock file, `next build`, `tsc --noEmit`, and a check that the export contains the pages FastAPI serves |
| `image · build and run` | Builds the production image (cached), imports the database, S3 and voice drivers, runs the in-image tests, starts the whole stack on a fresh database and fails on any container restart |

**When a pull request merges to `main`**, only the web and image jobs run. The
pull request's own run already tested the very same merged result (GitHub runs a
pull request merged into the current `main`), and the team's rule is that a branch
is up to date with `main` before it merges, so a second test run would repeat the
first. A manual run of this workflow (Actions tab → tests → Run workflow) runs
everything, slow tests included.

**The `slow` tests.** `SLOW` in `tests/conftest.py` lists the 27 test functions
that took 5 s or more when the suite was timed on 4 Nov 2026 (together 327 of
567 s, 58%): they build a rendered film and run it through edits, voices or the
whole pipeline. A pull request skips them. A plain `python -m pytest -q` runs all
of them, and Gate 1 requires that, with 0 failed, before every commit. The weekly
full matrix and a manual run also run them. A new heavy test is added to `SLOW` by
hand; `tests/unit/test_slow_registry.py` fails if a listed name no longer exists or
the marker does not select exactly the list.

**How the suite is split.** `TEST_SHARD=N/M` (read in `tests/conftest.py`) keeps
the Nth of M slices of whatever is going to run: the tests are sorted by id and
dealt out in turn, so the slices are equal in size, the same on every machine, and
every test is in exactly one of them (`tests/unit/test_shards.py`). Each slice
runs its tests one at a time, so the few tests that assert a wall-clock bound (a
1 s provider timeout must resolve in under 4 s) still see a quiet machine. It is
not `pytest-xdist`: measured on two CPUs (4 Nov 2026), two workers took 573 s
against 676 s for the serial run, because every test already uses both cores
(ffmpeg and the shot renderer are multi-threaded). Separate machines add cores;
extra workers on one machine mostly share them.

A pull request that changes only documentation (every changed file under
`docs/` or a Markdown file) skips those three jobs; nothing in the suite reads
either. A newer push to a pull request cancels its older run; a push to `main`
never cancels another.

**Every Monday at 22:00 UTC, and on demand** (`.github/workflows/full-matrix.yml`):

| Job | What |
|---|---|
| `windows-latest · python 3.11` | The whole suite, `slow` tests included, in one job, on the development OS (fonts and path separators differ) |
| `ubuntu-latest · python 3.12` | The whole suite, `slow` tests included, in one job, on the other supported Python |

Each runs the unfiltered, unsliced suite, so it also proves that the slices and the
`slow` list lose nothing.

A failing scheduled run opens one issue titled "The full test matrix (Windows,
Python 3.12) is failing", comments on it each time it keeps failing, and closes
it when a run passes. To run the full matrix by hand: Actions tab → full matrix →
Run workflow, or `gh workflow run full-matrix.yml`. It has no pull-request trigger
(each push to a pull request would spend about $0.28 of the free allowance), so
after a change to it or to the shared setup (`.github/actions/python-env`) run it
once by hand. Both workflows set Python, uv, ffmpeg and the locked dependencies up
through that one shared action.

**Why weekly and not nightly.** The account is on the GitHub Free plan, whose
Licensing page lists "2,000 Actions minutes/month" and, under "Not included",
protected branches and required status checks (seen 4 Nov 2026). A run is blocked
once the included amount is used up if no payment method is on file. GitHub's
billing page gives the same allowance as $12 and the per-minute rates: $0.006
(Linux, 2-core) and $0.010 (Windows, 2-core), so a Windows minute counts as about
1.7 Linux minutes. One Windows plus Linux 3.12 run takes about 35 minutes of
runner time (the Linux 3.12 job took 15 minutes on 3 Nov; Windows is estimated at
19), roughly $0.28 or 47 Linux-equivalent minutes. Nightly (30 runs) would use
about 1,400 of the 2,000 minutes on its own; weekly (about 4 runs) about 190.

GitHub does not enforce any of this: the repository is private on the free
plan, where branch protection and rulesets are not available (the API answers
"Upgrade to GitHub Pro or make this repository public", 4 Nov 2026), so no check
can be made required. "Green before merge" is the team's rule
(`.claude/CLAUDE.md`), kept by hand.

The first CI run found a real bug the laptop could not: choosing Kokoro on a
machine without its optional packages raised `ModuleNotFoundError` before the
check that explains what to install.

## Measurement, not assertion

Some claims cannot be unit-tested, so they are measured and the method is
kept:

- `scripts/benchmark.py` runs fixed prompts and reports time per phase, length
  against target, frame-exact sync, lines on cuts, and which provider served
  each image, so a silent downgrade shows up as a number. Results:
  [BENCHMARK.md](BENCHMARK.md). With `--testset eval/testset.jsonl` it runs
  25 labelled prompts instead, the way the creator interface does (plan, then
  render), and also records which model wrote each script and which engine
  voiced each line. The fields are explained in
  [eval/README.md](../eval/README.md).
- Camera smoothness by phase correlation on real shot clips (worst
  frame-to-frame jump 0.19 px).
- Audio ducking measured per frequency band (~6 dB under speech).
- Speaking rate calibrated against real voices: 2.2 words/second for edge-tts
  (M3, after the first benchmark found films 17% long), 2.6 for Kokoro, the
  shipped voice (DST-28, from 39 lines of DST-10 run R).
- Render speed on the production VM, sampled for CPU every 2 s, before and
  after shots ran in parallel (188 s → 136 s).

## Verified against real services

| Claim | How it was verified |
|---|---|
| Postgres path | The whole suite against a real Postgres 16; 40 jobs claimed by 4 processes at once with no duplicates |
| Cloudflare R2 | 31 assets uploaded by the worker and fetched back byte-identical; nightly backups pushed and a full "machine lost" restore from the bucket |
| Live models | Gemini, Groq and Cloudflare through `python main.py providers --check`; four provider bugs found and fixed |
| GitHub OAuth | The registered app's credentials checked against GitHub's token endpoint, then the full round trip by a person |
| Production | Two real films rendered on the VM, timed by stage |
