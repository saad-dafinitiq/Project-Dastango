# Models and providers

Which model does each job is configuration, not code. This page explains the
configuration file, the free keys worth adding, the voices, and how to turn on
the paid options when the budget allows.

## How it works

Agents ask for a **role**; `config/providers.yaml` lists the providers for
each role in order of preference, with the environment variables each needs.
A provider whose credentials are missing is skipped; one that fails at run
time hands over to the next; every chain ends in something that works offline.
`.env` holds only credentials.

```yaml
roles:
  image:
    - provider: cloudflare              # first choice
      model: "@cf/black-forest-labs/flux-1-schnell"
      params: {steps: 8}
      requires: [CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN]
      concurrency: 4                    # calls it tolerates at once
      retries: 3                        # attempts before the next provider
      timeout_s: 180                    # longest one call may wait
    - provider: agnes                   # second choice, dev/test only (see "Agnes AI" below)
      model: agnes-image-2.1-flash
      params: {tier: 1K}                # 1K at 16:9 is 1312x736
      requires: [AGNES_API_KEY]
      concurrency: 2
      retries: 3
      timeout_s: 120
    - provider: placeholder             # offline, always works
```

`requires: [A|B]` means either variable will do. `timeout_s` is optional:
without it a call may wait 180 s for a script, 60 s for an edit or a
translation, 120 s for an image and 60 s for a voice line. When the time runs
out the call counts as failed and the next provider takes over at once; a
timeout is never retried, because a provider that hung once will likely hang
again (see F1 in [REQUEST_TRACE.md](REQUEST_TRACE.md)). This covers MyMemory, the
last step of the `translate` chain, too (since DST-18); when it is the last
provider and runs out of time, that subtitle language is left out of the film
and named on the job's completion event, never shipped untranslated.

`retries` covers short hiccups: a 500, or a 429 that asks to slow down. A 429
that says the daily quota is used up (Cloudflare's "daily free allocation",
Gemini's `PerDay` quota, Groq's `TPD`/`RPD`, OpenRouter's `free-models-per-day`,
MyMemory's "for today") is not retried: the next provider takes over at once,
the failed call's trace has `daily_quota: true`, and the job's completion event
lists it under `daily_quota_used_up` (since DST-20; the rule is in
`shared/providers.py`). A 402 (no balance left) is not retried either (DST-22).
See what this machine would use, and why the rest are skipped:

```bash
python main.py providers           # the chain per role and what each needs
python main.py providers --check   # one real call per role, to verify keys
```

**Overrides.** `PROVIDERS_FILE=/path/to.yaml` swaps the whole file;
`PROVIDER_IMAGE=placeholder` (or `PROVIDER_STORY`, `PROVIDER_TTS`, …) forces
one provider for a role; `LLM_PROVIDER=mock` forces the offline script and
edit paths (the tests use this).

## The free setup

Everything below is free and needs no card. With none of it, the product
still runs: template scripts, keyword edits, placeholder images, and Kokoro or
online voices.

| Key in `.env` | Unlocks | Where |
|---|---|---|
| `GROQ_API_KEY` | Groq writes scripts first (open-weight gpt-oss-120b) and is the fast fallback for edits and translation (gpt-oss-20b / 120b) | [console.groq.com](https://console.groq.com/keys) |
| `GEMINI_API_KEY` | Gemini Flash reads edits and translates subtitles first, and is the last cloud fallback for scripts | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `OPENROUTER_API_KEY` | Second for scripts, third for edits and translation (`openrouter/free`) | [openrouter.ai](https://openrouter.ai/keys) |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | FLUX images, four at a time; 10,000 neurons a day ≈ 170 images | [dash.cloudflare.com](https://dash.cloudflare.com), a Workers AI token |
| `AGNES_API_KEY` | Agnes images behind Cloudflare, so a render is not left with placeholders when Cloudflare's daily allowance is gone. **Dev/test only, read the data terms below first** | [agnes-ai.com](https://agnes-ai.com) |
| `MYMEMORY_EMAIL` | Raises the free translation fallback from ~5,000 to ~50,000 characters a day | any address |
| `OLLAMA_HOST` | Local models through Ollama, fully offline | `ollama serve` |

What running it live taught, now handled in the adapters:

- Reasoning models (Gemini Flash, gpt-oss) spend small token budgets thinking
  and return nothing, so every call asks for at least 600 tokens.
- Cloudflare's FLUX rejects a `seed`; the provider drops it and retries once.
- Pollinations is switched off since DST-22 (its entry is commented out in
  `config/providers.yaml`, the adapter is still in `image_gen_tool.py`). A key
  with no budget left answers 402 and the keyless endpoint it falls back to
  serves a weaker model, one request per IP, which is why each provider has its
  own concurrency limit. To use it again, restore its entry above `openai`.
- Cloudflare's safety filter occasionally refuses an innocent prompt; the next
  provider draws that image.

## Agnes AI (image backstop, dev/test only)

Added in DST-22 so that when Cloudflare's daily allowance runs out, images
come from a real model instead of placeholders. It sits after Cloudflare and
the local options and before OpenAI. The adapter is `_provider_agnes`.

- **API** (official docs, read 2026-11-02): `POST https://apihub.agnes-ai.com/v1/images/generations`
  with `agnes-image-2.1-flash` (high detail) or `agnes-image-2.0-flash` (faster).
  Size is a tier (`1K` to `4K`) plus a ratio; at 1K, 16:9 is 1312x736. The
  adapter asks for Base64 so there is no second download, and cover-fits the
  result like every other provider. Seed and negative prompt are not
  documented, so neither is sent. Plain HTTP rather than the OpenAI client,
  because `response_format` has to sit inside a field called `extra_body`.
- **Limits:** the free tier is limited per minute (20 requests a minute at 1K,
  10 at 2K), with no daily cap stated for it. So a 429 here is a short rate
  limit and keeps its retries; DST-20's rule only fires on a message that names
  a daily window. A 402 means no balance and is not retried. The free output
  is described as a trial, and the terms let Agnes change or end it.
- **Data terms: read before adding the key.** Agnes's privacy policy says that
  on the free API tier, inputs and outputs may be used to train and improve its
  models by default, unless you opt out "where available" (by emailing
  `support@agnes-ai.com`; no steps are given). Paid Token Plan and Enterprise
  are excluded by default. Data may be processed in Singapore, the US and other
  places, with no fixed retention period; the terms are governed by Singapore
  law. Both pages still contain unfilled placeholders. The project treats
  confidentiality as a pass/fail gate, so **Agnes is for development and
  testing with synthetic prompts only**: put `AGNES_API_KEY` in a developer's
  `.env`, never on the server that renders other people's films. Without the
  key the provider is skipped, which is how the server behaves today.
- **Long term:** not decided here; the production image provider depends on the
  Bedrock decision.

## Voices

| Engine | Cost | Notes |
|---|---|---|
| **Kokoro** (default) | free, offline | Apache-2.0, 82M parameters on onnxruntime; no GPU, no PyTorch. `uv sync --extra voices` then `python scripts/get_kokoro.py` (≈340 MB, SHA-256 verified) and set `KOKORO_MODEL` / `KOKORO_VOICES`. The container does all of this itself. |
| Edge TTS | free, online | Microsoft neural voices, mapped to character archetypes |
| ElevenLabs | paid | `ELEVENLABS_API_KEY` |
| gTTS | free, online | Clear but flat |
| System voices (pyttsx3) | free, offline | Only where the OS has a speech engine (Windows, macOS, or Linux with `espeak-ng`) |
| Silent | free, offline | Silence of the right length, so timing stays correct (tests) |

The creator can choose the engine per film; the voice picker says which
engines work on this machine and why the others don't, and plays a cached
one-line sample before anything is rendered.

## Real motion and lip sync (paid, opt-in)

By default every shot is a still with a supersampled camera move: free,
offline and frame-exact. Animating stills well costs money per clip with every
provider, so these sit **behind** the free path:

| Provider | Role | Needs | Cost |
|---|---|---|---|
| fal.ai (Stable Video Diffusion, SadTalker) | `video`, `lipsync` | `FAL_KEY` | ~$1 trial credit, then per clip |
| Hugging Face | `video` | `HF_TOKEN` | free tier, noticeably lower quality |
| **Veo 3.1** (via the Gemini API) | `video` | `GEMINI_API_KEY` **and** `VIDEO_BUDGET_OK=1` | billed per second; not in the free tier |
| Replicate | `video`, `lipsync` | `REPLICATE_API_TOKEN` | per second |

Veo needs the second variable on purpose: a Gemini key held for the free text
models must never start a per-second video bill by itself. The real-motion
providers are unit-tested with mocked HTTP; none has been run end to end,
because each costs money per clip.

## Adding a provider

1. An adapter: a `_provider_<name>` method in
   `mcp/tools/vision_tools/image_gen_tool.py` for images, or a branch in
   `mcp/tools/llm_tools/llm_client.py` for a language model (anything
   OpenAI-compatible needs only a base URL and a key name).
2. An entry in `config/providers.yaml` under the role, with `requires`,
   `concurrency` and `retries`, and `timeout_s` if the role's default does not
   suit it. The adapter must pass `spec.timeout_s` to its HTTP call.
3. A test with the HTTP mocked, including what a failure looks like.

Never an `if os.getenv(...)` inside an agent. That is what this layer
replaced.
