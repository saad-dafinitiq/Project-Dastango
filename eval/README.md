# Evaluation test set

`testset.jsonl` holds 25 labelled film prompts, one JSON object per line. It is
the fixed input for measuring Dastango's quality: the same prompts are run
before and after a change, so the numbers can be compared.

Run them through the benchmark rather than by hand:

```bash
python scripts/benchmark.py --testset eval/testset.jsonl --render-subset   # the 8 full renders
python scripts/benchmark.py --testset eval/testset.jsonl --ids dst-03,dst-07
python scripts/benchmark.py --testset eval/testset.jsonl --offline         # all 25, no network
```

Each prompt brings its own length, scene count and subtitle language, so
`--duration`, `--scenes` and `--subtitle-lang` are ignored with `--testset`.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable key, `dst-NN`. Never reuse or renumber one: results are joined on it. |
| `prompt` | string | Exactly what a creator would type. Not trimmed or cleaned up, because the messy ones are the point. |
| `target_duration_s` | int | The length asked for, in seconds. The interface offers 20, 30, 45 and 60. |
| `expected_scenes` | int | The scene count asked for (the interface allows 2 to 6). The script should come back with exactly this many scenes, even when the prompt asks for something else (see `dst-20`). |
| `expected_characters` | int or null | How many characters the prompt names or clearly singles out (see below). `null` when the prompt does not decide it, and then character count is not scored for that prompt. |
| `subtitle_language` | string | The subtitle language chosen for the render. Must be a name from `shared/languages.py`. |
| `tags` | list of strings | `genre:...`, `tone:...` and any number of `edge:...` flags, listed below. |
| `render_subset` | bool | `true` for the 8 prompts that get a full render. The other 17 are measured at script and storyboard level only, which costs far less image quota. |

### How characters are counted

A character is a person, animal or creature that acts in the story and that
the prompt names or singles out. "Her grandson", "a stranded whale" and "the
cat" count. Groups do not ("her cricket team", "the neighbours"), and neither
do people who are only mentioned ("their mother's favourite vase").

**The narrator is never counted.** The story prompt always offers the model a
narrator, so counting it would make every label off by one. When scoring,
compare this number with the script's characters minus any with
`role: "narrator"`.

The story prompt also tells the model to "include 3-4 characters total". So a
prompt about one person will often come back with supporting characters the
prompt never asked for. That mismatch is real and is what this label is there
to measure. It is not a labelling mistake.

### Tones

`tone:` uses the pipeline's own scene vocabulary (`neutral`, `tense`,
`joyful`, `melancholic`), so a measured tone can be compared with the label
directly. `tone:unspecified` and `genre:unspecified` mark a prompt too short
to have either.

### Edge-case flags

| Flag | Prompts | What it tests |
|---|---|---|
| `edge:short-prompt` | dst-07 | A one-word prompt ("Rain"), right at the interface's 4-character minimum. |
| `edge:long-prompt` | dst-08 | 123 words with four named characters and a full plot. Does the script keep them? |
| `edge:non-english-prompt` | dst-05, dst-17, dst-18 | Urdu in Urdu script, Spanish, and Roman Urdu. |
| `edge:roman-urdu` | dst-18 | Urdu typed in Latin letters, as many Pakistani users write it. |
| `edge:safety-filter-risk` | dst-06, dst-19 | Innocent prompts with wounds, war or swimwear that an image safety filter may refuse. A refusal should fall through to the next provider, and that must show up in the results. |
| `edge:prompt-injection` | dst-25 | The prompt tells the model to ignore its instructions. The film should still be a film about a robot who paints. |
| `edge:prompt-conflicts-settings` | dst-20 | The prompt asks for ten scenes but the setting is 3. The setting should win. |
| `edge:narration-only` | dst-21 | Asks for only a narrator, so 0 characters are expected. |
| `edge:emoji` | dst-24 | Emoji and repeated punctuation in the prompt. |
| `edge:named-characters` | dst-08, dst-22 | Characters with names. Do those names reach the script? |
| `edge:many-characters` | dst-08, dst-12 | Four characters, more than the "3-4 characters total" rule leaves room for alongside a narrator. |
| `edge:min-scenes`, `edge:max-scenes` | several | The interface's limits of 2 and 6 scenes. |
| `edge:baseline-continuity` | dst-01, dst-09, dst-10 | The three prompts `scripts/benchmark.py` has always used, so new numbers can be set against old ones. |

## Coverage

- **Subtitle languages:** 9 in total. English (12 prompts), Urdu (4,
  right to left), Spanish (2), Hindi (2), Arabic (1, right to left), French,
  Japanese, Chinese and Korean (1 each). That covers Latin, Arabic-script,
  Devanagari and CJK fonts for the burned-in subtitles.
- **Lengths:** 20 s (4 prompts), 30 s (14), 45 s (5), 60 s (2).
- **Scenes:** 2 to 6, as the interface allows.
- **Genres:** sci-fi, family drama, horror, comedy, war, mystery, romance,
  heist, fantasy, historical, slice of life, sports, magical realism, action,
  nature, documentary, children's fable, and one with no genre at all.

### The render subset

The 8 prompts with `render_subset: true` are half ordinary films and half edge
cases, chosen so one full render of each exercises the paths most likely to
break:

| id | Why it is in the subset |
|---|---|
| dst-01 | Baseline continuity: the prompt the old benchmark numbers came from. |
| dst-02 | Urdu subtitles burned in (right to left), 45 s, 4 scenes. |
| dst-03 | Shortest setting: 20 s, 2 scenes, tense. |
| dst-04 | Comedy with Spanish subtitles. |
| dst-05 | A prompt written in Urdu script. |
| dst-06 | Likely to trip Cloudflare's image safety filter. |
| dst-07 | A one-word prompt, with Arabic subtitles. |
| dst-08 | The long prompt, with Hindi (Devanagari) subtitles. |

Together they ask for 24 scenes. A full render draws one image per character
plus three per scene (`image_budget` in
`agents/orchestrator/storyboard_view.py`), against a free Cloudflare allowance
of about 170 images a day. The count actually used, and which provider drew
each image, is recorded with every benchmark run.

## Changing the test set

Add prompts at the end with the next free id. If a label turns out to be
wrong, fix it in place and say so in the commit message, because results
measured against the old label are no longer comparable.
`tests/unit/test_eval_testset.py` checks the file's shape (fields, types,
unique ids, supported languages, exactly 8 in the subset, the required edge
cases) and fails if any of it breaks.
