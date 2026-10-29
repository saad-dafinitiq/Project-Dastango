# Evaluation: baseline quality on the test set

DST-10 baseline. Every number below comes from a run listed in this file, on
the date given. Raw results are in `data/benchmarks/` (not committed; `data/`
is ignored). Results from different commits or different provider conditions
are reported separately and never averaged together.

## Runs

| Run | Raw file(s) | When (PKT) | Code | What ran | Images | Scripts, translation | Voices |
|---|---|---|---|---|---|---|---|
| **P1** | `20261029_022957_testset.json` | 2026-10-29 02:29 | `2823ce3` + the `--plan-only` flag (uncommitted) | plan only, dst-01 to dst-05 | forced placeholder | chain Gemini → Groq → OpenRouter | none |
| **P2** | `20261029_024354_testset.json` | 2026-10-29 02:43 | same | plan only, dst-04 (rerun after outage) | forced placeholder | same | none |
| **P3** | `20261029_094803_testset.json` | 2026-10-29 09:48 | same | plan only, dst-05 to dst-25 | forced placeholder | same | none |
| **R** | `20261029_102217_testset.json` … `20261029_104625_testset.json` (one per film) | 2026-10-29 10:22 to 10:49 | same | full render, the 8 subset films, one at a time, 90 s apart | real chain: Cloudflare → Pollinations → placeholder | same chain; Gemini out of quota, so all Groq | Kokoro |
| **S** | `20261029_101803_storage_before.json` | 2026-10-29 10:16 | same | storage "before": dst-03, 3 edits | forced placeholder | same | Kokoro |
| **A** | `20261028_111752_testset.json` | 2026-10-28 11:17 | pipeline code `9cedcf7` (before DST-11 and DST-12) | full render, dst-01 to dst-06 | forced placeholder | Gemini first | Kokoro |
| **B** | `20261028_120715_testset.json` | 2026-10-28 12:07 | pipeline code `9cedcf7` | full render, dst-07 and dst-08 | forced placeholder | Gemini first | Kokoro |
| **H** | `20261028_dst07_gemini_hang.json` | 2026-10-28 11:29 | pipeline code `9cedcf7` | the dst-07 hang (inside run A) | | | |

- **Test set:** `eval/testset.jsonl`, 25 prompts, unchanged since commit `0bc83f4` (2026-10-28). Field guide: `eval/README.md`.
- **Command:** `python scripts/benchmark.py --testset eval/testset.jsonl` with `--plan-only` (P1 to P3), `--ids <one id>` (R), `--render-subset` (A).
- **Code:** reports from the 2026-10-29 runs record `2823ce3-dirty`. The uncommitted changes were the new `--plan-only` flag (`scripts/benchmark.py`, `tests/unit/test_eval_testset.py`) and this file; no pipeline code was changed. `2823ce3` includes DST-9, DST-11 (tracing) and DST-12 (model-call timeouts). Runs A and B predate both.
- **Machine:** the dev laptop (Windows 11), one run at a time.
- **Excluded:** P1's first tries of dst-04 and dst-05. The laptop lost its internet connection (DNS failing, first logged 02:39:15, back by 02:43:35), so every provider failed and both got the template script. The P2 and P3 reruns replace them. Run A0 (`20261028_111104_render_subset.log`) was stopped on purpose and measured nothing.

## Plan level: all 25 prompts (P1, P2, P3)

Images were forced to the placeholder, so **the storyboard placeholder rate
is not measured** (it is 100% by construction).

"Planned length" is the dialogue estimate the timeline itself makes before any
voice exists (`estimate_line_ms` per line plus `scene_overhead_ms` per scene,
`shared/timeline.py`). The storyboard's own `estimated_ms` is not used: it is
the model's `duration_ms` field, which the story prompt sets to target ÷ scene
count, so it always equals the target (see Findings).

| id | run | plan time | scenes (got/asked) | characters (got/label) | script by | target | planned length | error |
|---|---|---|---|---|---|---|---|---|
| dst-01 | P1 | 18.3 s | 3/3 | 3/1 | groq ¹ | 30 s | 26.1 s | 13.0% |
| dst-02 | P1 | 169.2 s | 4/4 | 3/2 | gemini | 45 s | 44.9 s | 0.3% |
| dst-03 | P1 | 186.8 s | 2/2 | 2/1 | groq ² | 20 s | 16.5 s | 17.4% |
| dst-04 | P2 | 88.3 s | 3/3 | 3/2 | gemini | 30 s | 29.6 s | 1.4% |
| dst-05 | P3 | 91.8 s | 3/3 | 2/2 | gemini | 30 s | 31.3 s | 4.5% |
| dst-06 | P3 | 189.2 s | 3/3 | 3/2 | groq ² | 30 s | 31.2 s | 3.9% |
| dst-07 | P3 | 92.4 s | 2/2 | 3/unlabelled | gemini | 20 s | 22.0 s | 9.8% |
| dst-08 | P3 | 188.5 s | 4/4 | 4/4 | groq ² | 45 s | 39.9 s | 11.4% |
| dst-09 | P3 | 187.2 s | 4/4 | 3/1 | groq ² | 45 s | 22.2 s | 50.7% |
| dst-10 | P3 | 95.0 s | 3/3 | 2/2 | gemini | 30 s | 30.4 s | 1.4% |
| dst-11 | P3 | 188.1 s | 3/3 | 2/2 | groq ² | 30 s | 27.9 s | 7.0% |
| dst-12 | P3 | 189.4 s | 6/6 | 4/4 | groq ² | 60 s | 53.2 s | 11.4% |
| dst-13 | P3 | 187.9 s | 4/4 | 3/1 | groq ² | 45 s | 32.6 s | 27.6% |
| dst-14 | P3 | 50.9 s | 5/5 | ⚠ template | ⚠ **template** ³ | 60 s | ⚠ | ⚠ |
| dst-15 | P3 | 7.7 s | 2/2 | 2/1 | groq ⁴ | 20 s | 17.8 s | 10.8% |
| dst-16 | P3 | 8.5 s | 3/3 | 3/1 | groq ⁴ | 30 s | 19.3 s | 35.6% |
| dst-17 | P3 | 55.3 s | 3/3 | 3/2 | openrouter ⁴ | 30 s | 37.3 s | 24.5% |
| dst-18 | P3 | 5.8 s | 3/3 | 2/2 | groq ⁴ | 30 s | 29.9 s | 0.2% |
| dst-19 | P3 | 4.4 s | 3/3 | 2/2 | groq ⁴ | 30 s | 19.7 s | 34.2% |
| dst-20 | P3 | 5.2 s | 3/3 | 2/unlabelled | groq ⁴ | 30 s | 25.2 s | 15.9% |
| dst-21 | P3 | 40.6 s | 4/4 | ⚠ template | ⚠ **template** ³ | 45 s | ⚠ | ⚠ |
| dst-22 | P3 | 8.2 s | 3/3 | 3/3 | groq ⁴ | 30 s | 21.5 s | 28.2% |
| dst-23 | P3 | 56.0 s | 3/3 | ⚠ template | ⚠ **template** ³ | 30 s | ⚠ | ⚠ |
| dst-24 | P3 | 6.4 s | 2/2 | 2/1 | groq ⁴ | 20 s | 19.7 s | 1.5% |
| dst-25 | P3 | 5.8 s | 3/3 | 1/1 | groq ⁴ | 30 s | 29.9 s | 0.2% |

¹ Gemini answered HTTP 503; Groq wrote the script.
² Gemini used its full 180 s story limit (DST-12) and failed; Groq wrote the script. Confirmed in the logs for all seven.
³ **All three providers refused** (logged for each): Gemini's daily quota was used up (`429 RESOURCE_EXHAUSTED`), Groq's first answer failed schema validation and its retry hit the tokens-per-minute limit (`429`), and OpenRouter's free model cut its answer short (`finish_reason=length`). The story agent used its fixed template, so these rows say nothing about script quality and are left out of the summary.
⁴ Gemini's daily quota was used up from 10:11:35 (first `429 RESOURCE_EXHAUSTED`, at dst-14); from dst-14 on, every prompt was effectively Groq first.

### Plan-level summary (22 model-written scripts)

| Measure | Result |
|---|---|
| Scene count as asked | **22 of 22**, including dst-20, whose prompt asks for "ten scenes" when the setting is 3 |
| Character count as labelled | **9 of 20** labelled prompts exact; **11 over, 0 under** |
| Planned length vs target | **17 of 22 shorter** than the target; mean error 14.1%, median 11.1%; worst dst-09, 22.2 s planned for 45 s |
| Plan time (all 25) | 4.4 to 189.4 s, median 56.0 s. Seven prompts took 186.8 to 189.4 s (Gemini at its 180 s limit, then Groq); Groq-only prompts took 4.4 to 8.5 s |
| Who wrote the scripts (all 25) | Groq 16, Gemini 5, OpenRouter 1, template 3 |
| Storyboard placeholder rate | **Not measured** (images forced to placeholder) |

## Render level: the 8 subset films with real images (R)

Run one film at a time, 90 s apart, so Groq's tokens-per-minute window could
reset between scripts. Every script was written by Groq: Gemini's daily quota
was already used up, so each film's Gemini call failed at once.

| id | subtitles | plan | render | film / target | length error | in sync | lines on a cut | images by provider | placeholder rate |
|---|---|---|---|---|---|---|---|---|---|
| dst-01 | English | 48.6 s | 66.5 s | 27.2 / 30 s | 9.4% | yes (652/652 frames) | 8/8 | Cloudflare 12 | 0% |
| dst-02 | Urdu | 23.2 s | 78.0 s | 34.3 / 45 s | 23.7% | yes (824/824) | 6/6 | Cloudflare 15 | 0% |
| dst-03 | English | 14.7 s | 42.7 s | 14.2 / 20 s | 29.0% | yes (341/341) | 4/4 | Cloudflare 8, Pollinations 1 ⚠ | 0% |
| dst-04 | Spanish | 17.0 s | 57.2 s | 24.0 / 30 s | 19.9% | yes (577/577) | 7/7 | Cloudflare 12 | 0% |
| dst-05 | Urdu | 16.3 s | 196.6 s | **150.8 / 30 s** | **402.6%** | yes (3619/3619) | 6/6 | Cloudflare 12 | 0% |
| dst-06 | English | 41.1 s | 111.7 s | 21.8 / 30 s | 27.3% | yes (523/523) | 3/3 | ⚠ Pollinations 11, placeholder 1 | 8.3% |
| dst-07 | Arabic | 24.1 s | 53.9 s | 16.1 / 20 s | 19.3% | yes (387/387) | 4/4 | ⚠ Pollinations 1, placeholder 8 | 88.9% |
| dst-08 | Hindi | 33.5 s | 96.2 s | 33.4 / 45 s | 25.9% | yes (801/801) | 7/7 | ⚠ Pollinations 2, placeholder 15 | 88.2% |

⚠ = affected by a provider fallback: dst-03 lost one image to a Cloudflare
safety-filter refusal; from dst-06 on, Cloudflare's daily quota was used up
and then Pollinations' balance ran out (see "Free quota used").

### Render-level summary

| Measure | Result |
|---|---|
| Audio/video sync | **8 of 8 frame-exact** |
| Lines starting on a cut | **45 of 45** |
| Film length vs target | **7 of 8 shorter** than asked; mean error **22.1%** without dst-05, 69.6% with it; median 24.8% |
| Render time | 42.7 to 196.6 s, median 72.25 s |
| Images | 98 in total: **Cloudflare 59, Pollinations 15, placeholder 24**; placeholder rate 24.5% overall, **0% while Cloudflare had quota (dst-01 to dst-05)** |
| Character count as labelled | 5 of 7 exact, 2 over |
| Voices | Kokoro spoke all 45 lines; no voice fallback |

### Earlier render runs, 2026-10-28 (A, B): reported separately

Pipeline code `9cedcf7`, before DST-11 and DST-12; placeholder images; Gemini
first (Gemini wrote 4 of the 8 scripts, Groq the other 4 after Gemini
returned 503). Not comparable with run R: different code, different script
writers, no real images.

| id | plan | render | film / target | length error | in sync | lines on a cut |
|---|---|---|---|---|---|---|
| dst-01 | 17.8 s | 40.9 s | 27.1 / 30 s | 9.6% | yes | 6/6 |
| dst-02 | 31.8 s | 43.2 s | 31.3 / 45 s | 30.4% | yes | 7/7 |
| dst-03 | 7.1 s | 24.9 s | 20.1 / 20 s | 0.4% | yes | 6/6 |
| dst-04 | 84.3 s | 57.1 s | 26.1 / 30 s | 13.0% | yes | 5/5 |
| dst-05 | 112.5 s | 247.2 s | **210.0 / 30 s** | **600.1%** | yes | 6/6 |
| dst-06 | 10.2 s | 21.0 s | 17.3 / 30 s | 42.2% | yes | 4/4 |
| dst-07 | 12.2 s | 40.1 s | 18.8 / 20 s | 6.0% | yes | 5/5 |
| dst-08 | 114.8 s | 139.9 s | 41.8 / 45 s | 7.1% | yes | 8/8 |

8 of 8 in sync, 47 of 47 lines on a cut, 7 of 8 shorter than asked, mean
length error 15.5% without dst-05. dst-07 is from run B, a rerun after its
first attempt hung (Findings, 1).

## Storage growth "before" (S)

One film, dst-03 (20 s, 2 scenes), placeholder images (reproducible, so the
"after" measurement for content-addressed storage can repeat the same film),
project `20261029_051640_8392c4`. Only that project's folders are counted.
The script was Groq's; all 3 edits succeeded.

| Step | `data/outputs/<project>` | `data/state_versions/<project>` | Total | Time |
|---|---|---|---|---|
| After plan (v1) | 0.06 MB, 8 files | 0.07 MB, 9 files | **0.14 MB** | 11.6 s |
| After render (v2) | 9.59 MB, 44 files | 6.22 MB, 39 files | **15.80 MB** | 27.8 s |
| After edit 1, "make the voices in scene 2 whispered" (v3) | 10.79 MB, 46 files | 12.99 MB, 69 files | **23.78 MB** | 17.3 s |
| After edit 2, "make scene 1 darker" (v4) | 10.26 MB, 48 files | 19.50 MB, 101 files | **29.77 MB** | 13.8 s |
| After edit 3, "make everyone louder" (v5) | 10.26 MB, 48 files | 26.02 MB, 133 files | **36.28 MB** | 12.7 s |

**Three edits took the film from 15.80 MB to 36.28 MB (2.3x).** The film
itself stays about 10 MB; each new version adds 6.5 to 6.8 MB to
`state_versions`, because every version copies every file (F3).

## Free quota used, 2026-10-29

| Provider | What happened |
|---|---|
| **Cloudflare** (images) | **59 images** in run R before its daily allowance ran out: the first `429` ("you have used up your daily free allocation of 10,000 neurons") was at 10:39:43, during dst-06. The allowance is about 170 images a day and is shared with the live VM, whose use is not visible here. 1 refusal as NSFW (dst-03, `HTTP 400`, code 8007) |
| **Pollinations** (images) | **15 images.** The keyed endpoint answered `402` from 10:41:26. A single check at 10:54 (the only extra call) returned the reason: *"Insufficient balance. This request costs ~0.0040 pollen, but your available balance is 0.0020"* (`INSUFFICIENT_BALANCE`). The key's own budget showed 99.752 pollen on the dashboard; that is the key's spending limit, not the account's balance. The keyless fallback then mostly answered `402` and served a weaker model (`sana`, 1024x576) for the one image it drew |
| **Placeholder** (offline) | 24 images in run R; every preview in P1 to P3 and S by design |
| **Gemini** (scripts, translation) | Daily quota used up: first `429 RESOURCE_EXHAUSTED` at 10:11:35. Before that, 5 scripts written and 7 calls stopped at the 180 s limit |
| **Groq** (scripts, translation) | Wrote 16 of the plan-pass scripts and all 8 render scripts, plus the render translations; 4 tokens-per-minute `429`s in run P3 |
| **OpenRouter** | 1 script (dst-17); 3 answers cut short (`finish_reason=length`) |
| **Kokoro** (voices) | Local, no quota |

## Findings

1. **A model call could hang forever (fixed by DST-12).** In run A on 2026-10-28,
   dst-07's `story` call to Gemini hung for **2,164 s (36 min 4 s)**, from
   11:29:42.942 until stopped by hand; with no timeout set, google-genai waits
   forever (`20261028_dst07_gemini_hang.json`). On 2026-10-29 the same kind of
   call stopped at the 180 s limit seven times in runs P1 and P3, and Groq
   took over each time.
2. **A non-English prompt makes a broken film that reports success.** dst-05
   (a prompt in Urdu script) got Urdu dialogue and Kokoro, which has no Urdu
   voice, stretched every line: **210.0 s for a 30 s target** in run A
   (Gemini's script) and **150.8 s** in run R (Groq's), both "in sync" with no
   warning. In run A its lines rendered 7 to 13 times longer than estimated
   (a 7-word line estimated at 2.8 s rendered at 29.1 s).
3. **Films come out shorter than asked, and the plan already shows it.** 7 of
   8 renders were short in both run A and run R (mean error 15.5% and 22.1%
   without dst-05), and 17 of 22 plans were already short at the dialogue
   estimate (median 11.1%).
4. **The storyboard's length estimate just repeats the target.** Every
   storyboard frame's `estimated_ms` is the model's `duration_ms`, which the
   prompt sets to target ÷ scene count (`agents/video_agent/agent.py:340`),
   so the length shown before rendering is always exactly the target.
5. **Scripts add characters the prompt did not ask for**: 11 of 20 labelled
   plans came back with more characters, none with fewer, as the story
   prompt's "3-4 characters total" rule predicts.
6. **The free tiers can all run out within one session.** Within about 30
   minutes on 2026-10-29: Gemini's daily quota (10:11), Cloudflare's daily
   allowance (10:39) and Pollinations' balance (10:41). Three plans fell back
   to the template script, and the last two films were mostly placeholders,
   all reported as successful runs.
7. **Pollinations' real error was invisible.** The app logs any `401`/`402`/
   `403` from the keyed endpoint as "key rejected" and drops the response
   body, so the actual reason (`INSUFFICIENT_BALANCE`) only surfaced with a
   separate check.
8. **A failed JSON answer can trip Groq's rate limit.** In all three template
   cases, Groq's first answer failed schema validation and the automatic retry
   was refused with the tokens-per-minute `429`.

## Not measured

- **Storyboard placeholder rate with real images**: the plan pass used
  placeholders to save image quota.
- **Image metrics for dst-06, dst-07 and dst-08**: their images came from
  Pollinations or the placeholder because the free image quotas ran out, so
  they measure quota exhaustion, not image quality.
- **The safety-filter prompts with Cloudflare**: dst-06 never reached
  Cloudflare (its quota was gone) and dst-19 was only planned. The one
  safety-filter refusal seen was a false positive on dst-03.
- **Script quality for dst-14, dst-21, dst-23**: template scripts.
- **Run-to-run variation**: every prompt ran once per run.
- **A Gemini-first render on today's code**: Gemini's quota was used up
  before run R.
- **Character consistency across images**: needs a visual check, not done.
- **The same runs on the production VM.**
