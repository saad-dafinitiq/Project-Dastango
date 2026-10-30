# Tracing model calls

Every call Dastango makes to a model (a script or translation from an LLM, an
image, a voice line) can be recorded in [Langfuse](https://langfuse.com), an
open-source tracing server used as Langfuse Cloud or self-hosted. Tracing is
**off** unless it is configured, and the app runs unchanged without it.

The structure follows Langfuse's own
[observability best practices](https://langfuse.com/docs/observability/best-practices),
audited against a live trace with the `langfuse` agent skill
(github.com/langfuse/skills).

## How a film reads in Langfuse

An illustration of the shape (the measured trace is at the end of this page):

```
session  <project id>                        one film: its plan, render and edits
└─ trace  render-film                        one job   (tag: render, user: owner id)
   └─ render-film  (chain)                   input: settings · output: outcome
      ├─ write-script        (generation)    gemini, failed: ServerError
      ├─ write-script        (generation)    groq, fallback, 78/17 tokens
      ├─ generate-image      (generation)    cloudflare, 1280x720
      └─ synthesize-voice    (generation)    kokoro
```

- **One trace per job**, with a root `chain` observation named for the job
  (`plan-storyboard`, `render-film`, `edit-film`, `revert-version`,
  `make-film`, `rerun-phase`). Every model call made during the job is nested
  under it, including calls made in parallel worker threads.
- **One generation per provider attempt**, named for what it does:
  `write-script`, `classify-edit`, `translate-subtitles`, `generate-image`,
  `synthesize-voice`. A fallback shows as a failed generation followed by one
  with `fallback: true`. Names are stable: providers, models and ids are
  attributes, never part of a name, so dashboards and filters keep working.
- **One session per film** (`session_id` = project id), the **job kind as a
  tag**, and the film owner's **internal account id as `user_id`** (never an
  email or name), for per-account views and cost.
- **Environment**: set `LANGFUSE_TRACING_ENVIRONMENT` (`development` on a
  laptop, `production` on the VM) so test runs do not mix with real ones.

## What is sent, and what never is

| Where | Contents |
|---|---|
| Generation attributes | `model`; token usage when the provider reports it (Gemini, the OpenAI-compatible APIs, Anthropic); level ERROR with the error's class name on failure |
| Generation metadata | `provider`, `role`, `position` (place in the chain), `fallback`, `attempt`, `ok`, `error`, `timed_out`, `http_status` (the provider's HTTP answer on failure, e.g. 503 overloaded, 504, 429 quota), `daily_quota` (true when that 429 meant the day's allowance is used up, so the call was not retried), `latency_ms`, `job_id`, `job_kind`, `project_id`. A failed generation's status reads e.g. `ServerError (HTTP 503)` or `RuntimeError (HTTP 429, daily quota used up)` |
| Generation input / output | Sizes only: prompt and system lengths, `max_tokens`, schema name, answer length, image width and height, voice id and line length |
| Job input | The job's settings: length, scene count, subtitle language, voice engine, music and subtitle switches. The creator's prompt or edit request becomes its **character count** |
| Job output | Stage, version, scene count, film length |

**Never sent**: prompts, scripts, answers, edit requests, images, audio, keys or
error messages. Every field passes an allow-list in `shared/tracing.py`
(`SPAN_FIELDS`, `IO_FIELDS`, `JOB_INPUT_FIELDS`, `JOB_OUTPUT_FIELDS`), and any
credential value from the environment is blanked out of the strings that
remain, as a second line of defence. The Langfuse SDK itself adds its own
OpenTelemetry attributes (service name, SDK versions and the project's
**public** key); the secret key is not sent.

## Where it is wired in

Three call sites and the worker, nothing else:

- `mcp/tools/llm_tools/llm_client.py` wraps each provider attempt; each
  adapter reports the provider's token counts.
- `mcp/tools/vision_tools/image_gen_tool.py` wraps each provider attempt,
  after it has a concurrency slot, so queueing is not counted as latency.
- `mcp/tools/audio_tools/tts_tool.py` wraps each engine on its fallback ladder.
- `jobs/worker.py` runs each job inside `tracing.job_trace(...)` with its
  settings and owner, and reports the outcome.

Images and voices are made in parallel threads (`shared/utils/parallel.py`),
which copies the caller's context into each thread so their generations still
nest under the job. A failure inside tracing (server down, SDK error) is logged
once and ignored: it never stops a film.

Calls made outside a job, from the CLI or `scripts/benchmark.py`, are traced
one by one, without a job, session or root.

## Turning it on

1. Install the optional package (pinned to the version the code was written
   against):

   ```bash
   pip install -r requirements-tracing.txt
   ```

2. Pick a Langfuse server: a Langfuse Cloud project (what Dastango uses today,
   on the owner's account), or a self-hosted one from Langfuse's own
   docker-compose. Only what the table above lists is sent either way.

3. In the Langfuse UI create a project and an API key pair (Settings → API
   Keys), and put the values in `.env` (names only are in `.env.example`; the
   keys never go in the repository):

   ```bash
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_BASE_URL=https://us.cloud.langfuse.com   # the region your project is in
   LANGFUSE_TRACING_ENVIRONMENT=development
   ```

   `LANGFUSE_BASE_URL` is required. Without it the Langfuse SDK silently
   defaults to Langfuse Cloud, so Dastango keeps tracing off and logs a warning
   instead: where traces go is always a deliberate setting.

4. Restart the app or worker. The log says `tracing model calls to <url>`.

Tests never trace: `tests/conftest.py` removes the `LANGFUSE_*` variables, and
`tests/unit/test_model_call_tracing.py` checks what would be sent with a fake
tracer.

## Verified live (2026-10-28)

A job's worth of calls (one LLM call, one image, one voice line) sent through
`job_trace` to Langfuse Cloud (US region) from the DST-11 branch, on top of
commit `250adcf` with the changes not yet committed, then read back through
Langfuse's API and audited against the best-practices page:

| Observation | Type | Parent | Level | Model | Notes |
|---|---|---|---|---|---|
| `render-film` | chain | (root) | DEFAULT | | input: scene count, subtitle language, `prompt_chars: 19`; output: stage, version |
| `classify-edit` | generation | `render-film` | ERROR | gemini-flash-latest | `ServerError` (503 in one run; a `504 DEADLINE_EXCEEDED` at the 60 s limit in another) |
| `classify-edit` | generation | `render-film` | DEFAULT | openai/gpt-oss-20b | `fallback: true`, position 2, 1,469 ms, 78 / 17 / 95 tokens |
| `generate-image` | generation | `render-film` | DEFAULT | placeholder | 64x36 in and out, 31 ms |
| `synthesize-voice` | generation | `render-film` | DEFAULT | silent | 7 characters, 62 ms |

Every observation carried the session (project id), user id, trace name
`render-film`, tag `render` and environment `development`. A search of
everything returned found neither the prompt text nor the secret key. The
audit's one gap, `environment: default`, is closed by
`LANGFUSE_TRACING_ENVIRONMENT`.

A Gemini failure at our own deadline is recorded as `timed_out: false`: Google
answered with an HTTP 504, and a provider that answered is not counted as a
timeout. Since these runs, failed generations also record `http_status`, so
that case now reads `ServerError (HTTP 504)` and an overloaded Gemini
`ServerError (HTTP 503)`; before, both showed only `ServerError`.
