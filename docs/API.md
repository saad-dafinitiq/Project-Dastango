# API reference

Everything the web app does goes through this API; the CLI calls the same
orchestrator directly. The interactive reference is served at `/api/docs` and
the OpenAPI schema at `/api/openapi.json` (`/docs` is the product's own guide).

**Conventions**

- **Same origin, cookie auth.** Sign in sets an HttpOnly session cookie
  (`storygen_session`, `Secure` over HTTPS, 14 days). Every `/api/*` route
  except `/api/auth/*` requires it, enforced on the router, not per endpoint.
- **Someone else's project is a `404`**, never a `403`: a different status
  would confirm the id exists.
- **Long work is a job.** Endpoints that start work return at once with
  `{project_id, job_id, status, websocket}`; follow progress on the WebSocket
  or poll `/api/jobs/{job_id}`.
- Errors are `{"detail": "…"}` with a sentence a person can act on.

---

## Health

| Method | Path | Auth | |
|---|---|---|---|
| GET | `/health` | none | Liveness: the process is up |
| GET | `/ready` | none | Readiness: the database answers. `{"status", "database", "storage", "inline_worker"}` |

## Accounts: `/api/auth`

| Method | Path | Auth | |
|---|---|---|---|
| GET | `/status` | none | `{authenticated, user, needs_setup, signups_allowed, min_password_length, github: {enabled, connected}}` |
| POST | `/register` | none | `{email, password}`. The first account becomes the admin; after that `403` unless `ALLOW_SIGNUPS=1` |
| POST | `/login` | none | `{email, password}`. Wrong password and unknown address give the same `401`; 8 failures lock for 15 min |
| POST | `/logout` | session | Deletes the session row |
| GET | `/me` | session | The signed-in user |
| POST | `/password` | session | `{current_password, new_password}`; ends every other session |
| GET | `/sessions` | session | Your active sessions |
| GET | `/github/start?mode=signin\|connect` | none | Redirects to GitHub with a `state` bound to an HttpOnly cookie |
| GET | `/github/callback` | none | Checks `state`, exchanges the code, signs in or connects; redirects to `/?connected=github` or `/?auth_error=…` |
| GET | `/users` | admin | All accounts |
| POST | `/users` | admin | `{email, password, role}` |
| PATCH | `/users/{user_id}` | admin | `{role?, is_active?}`; an admin cannot demote or disable themselves |

## Making a film: `/api/pipeline`

| Method | Path | Body | |
|---|---|---|---|
| POST | `/plan` | `{prompt, target_duration_s=45 (15 to 600), scene_count=4 (1 to 12), format="16:9"\|"9:16"\|"1:1", style="realistic"\|"anime", with_preview=true}` (an unknown format or style is `400`, a number out of range `422`) | Queue a storyboard: script, one portrait per character and one preview per scene |
| GET | `/storyboard/{project_id}?engine=` | none | Scene cards (tone, camera move, per shot `transition` (how it comes in: `kind`, `ms`, `chosen`, `between`; null for the first shot of the film), `characters` who speak in the scene, lines and the voice that will speak each), the cast (per character: `name`, `role`, `description`, `voice`, `portrait_url`, `drawn`; `drawn` is false when the picture is only a stand-in), image budget, the script's length check (`length`), and `spec`: `{format, style, scene_check}` where `scene_check` says whether the script has the scene count that was asked for (`asked`, `got`, `repairs`, `merged`, `within`) |
| PATCH | `/storyboard/{project_id}/{scene_id}` | `{title?, setting?, visual_prompt?, dialogue?: {line_id: text}}` | Edit one scene; a new `visual_prompt` redraws only that scene's preview (one image). The words are saved as a version even if the picture could not be drawn: the answer then has `redraw: {ok: false, message}` and the previous picture is kept (`redraw: {ok: true}` otherwise, absent for a words-only edit). `409` while a plan, render or edit is running for the film. Preview addresses end in `?v=` and change only when the picture does |
| PATCH | `/spec/{project_id}` | `{format?, style?}` | Change a storyboard's format and/or style (DST-34): queues a `respec` job that draws every preview and portrait again in the new look and shape (the image count is `images.plan`) and keeps the words, the length and the creator's shot choices. `409` once the film is rendered (it is not converted: make a new film) or while a job runs; `400` for an unknown value. Pictures that could not be drawn are named in the completion event (`restyle_failed`) and are drawn fresh at render |
| GET | `/shot-options` | none | The camera moves (`id`, `label`, `about`) and framings a creator can choose for a shot |
| PATCH | `/storyboard/{project_id}/{scene_id}/shots/{shot_id}` | `{move?, framing?: wide\|medium\|close, reset?}` | Choose one shot's camera move and/or framing (DST-38). Before a render it saves a storyboard version and answers with the storyboard. On a finished film the film must be cut again, so it queues a `shot` job and answers `{job_id, …}` (progress streams like an edit; only the changed scene is re-rendered, no images are drawn). A framing the shot cannot have (a close-up with nobody speaking) is ignored. `400` for an unknown move or framing, `409` while the film is being worked on |
| POST | `/render/{project_id}` | `{with_bgm, with_subtitles, subtitle_language, burn_subtitles, tts_engine?}` | Queue the render of an approved storyboard |
| POST | `/run` | `{prompt, target_duration_s, scene_count, format, style, with_bgm, with_subtitles, subtitle_language, tts_engine?}` | Plan and render in one job |
| POST | `/rerun` | `{project_id, phase: story\|audio\|video}` | Re-run a phase and everything after it, keeping the film's settings |
| GET | `/film/{project_id}` | none | What the player needs: `video_url`, duration, size, version, `cut` (which cut this is for the creator: the first render is 1), `stand_in_scenes` (scene ids drawn with a placeholder because every image service was at its limit), and scenes as chapters with `start_ms`/`end_ms` and a poster; `format` and `style` the film was made in |
| GET | `/subtitles/{project_id}` | none | The film's WebVTT tracks for the player: language, URL, and which one is burned in |
| GET | `/languages` | none | The 14 subtitle languages |
| GET | `/state/{project_id}` | none | The full `PipelineState` |
| GET | `/status/{project_id}` | none | A lightweight status snapshot |

`tts_engine` is validated before a job exists: an unknown engine is `400`, one
this machine cannot run is `409` with the reason ("run
`python scripts/get_kokoro.py`").

## Changing a film: `/api/edit` and `/api/history`

| Method | Path | Body | |
|---|---|---|---|
| POST | `/api/edit/{project_id}` | `{query}` | Queue an edit (one attempt). `409` if the film has not been rendered |
| GET | `/api/edit/ops` | none | Every editing operation (DST-39) with its parameter schema and its choices: the transition styles (`id`, `label`, `about`, `main`) and speeds. What the storyboard's pickers draw from |
| POST | `/api/edit/{project_id}/op` | `{op, params}` | Apply one editing operation, as a click does (a typed sentence is turned into the very same operation). Before a render it changes the storyboard at once and answers with it; on a finished film it queues an `op` job (the film is cut again, only the scenes whose plan changed are re-rendered) and answers `{job_id, ...}`. `set_transition` params: `{scene, shot?, transition, speed? \| ms?, reset?}`; `set_camera`: `{scene, shot_id, move?, framing?, reset?}`. Sound (needs a rendered film, except a mood): `set_music` `{scene?, mood? \| track? \| off? \| reset?}`, `set_volume` `{target: music\|voices\|ambience, percent}` (100 = as made), `set_ambience` `{scene?, kind: rain\|wind\|city\|sea\|night\|none \| reset?}`. Titles: `set_graphics` `{title_card?, title_text?, scene_titles?, lower_thirds?, end_card?, end_text?, credits_text?, reset?}`. Look: `set_effect` `{scene?, effect: grain\|vignette\|glow\|letterbox\|glitch\|none, strength?: soft\|normal\|strong}` (drawn with MoviePy over a scene's clip; only the scenes that change are cut again). Scenes and shots: `regenerate_shot` `{scene, shot}` (needs a film), `edit_line` `{scene, line, text}` (records just that line again), `set_voice` `{character, tone? \| different?}`, `remove_scene` `{scene}`, `move_scene` `{scene, to}`. `400` unknown operation or bad parameters, `404` it does not fit this film (no such scene or shot, nothing before the first shot), `409` while the film is being worked on |
| GET | `/api/edit/music` | none | The music library (`id`, `title`, `mood`, `seconds`, `licence`), the moods, the ambience kinds (`rain`, `wind`, `city`, `sea`, `night`) and the upload limits |
| GET | `/api/edit/music/{track_id}/file` | none | A library track as MP3, to listen before choosing it |
| POST | `/api/edit/{project_id}/music` | multipart `file`, `rights=true` | Add your own music to this film. Checked (audio extension, at most 15 MB, 3 s to 10 min, has audio, not a video, decodes) and kept as a normalised mono MP3. Answers `{track: "upload:<id>", title, seconds}`; nothing changes until `set_music` uses it. `400` with the reason, `413` over the size limit, `404` for someone else's film |
| POST | `/api/edit/classify` | `{query, project_id?}` | What the query would be understood as, without changing anything |
| POST | `/api/edit/apply` | `{project_id, query}` | Queue an edit and wait for it (kept for scripts and the classic page) |
| GET | `/api/edit/log/{project_id}` | none | Every edit with its intent and result |
| GET | `/api/history/{project_id}` | none | The version list |
| GET | `/api/history/{project_id}/film` | none | Versions that produced a film, in the creator's words, with which is current; `number` is the creator's count (the first render is 1) where `version` also counts the storyboard |
| POST | `/api/history/{project_id}/revert/{version}` | none | Go back: restores that version's state and files as a **new** version |

## Jobs: `/api/jobs`

| Method | Path | |
|---|---|---|
| GET | `/?project_id=&status=` | Your jobs (admins see all) |
| GET | `/{job_id}` | Status, attempts, error |
| GET | `/{job_id}/events` | Every progress event the job emitted |
| POST | `/{job_id}/cancel` | A queued job stops now; a running one at its next step |

## Library and voices

| Method | Path | |
|---|---|---|
| GET | `/api/projects/` | Your films: title, prompt, stage, version, scene count, duration, poster frame and video URL |
| GET | `/api/voices/` | Each engine, whether it works on this machine and why not, and its voices |
| POST | `/api/voices/preview` | `{engine, voice, text?}` → a cached one-line sample. If the engine fell back, the response says so |

## Files

| Method | Path | |
|---|---|---|
| GET | `/assets/{project_id}/{path}` | A project's file, after an ownership check and a traversal check; answers `Range` requests so the player can seek |

---

## Progress over WebSocket

`WS /ws/progress/{project_id}`: authenticated by the same cookie on the
handshake; a stranger is closed with code `1008`.

On connect the server replays the project's current job from its first event,
then streams new ones as they are written (it reads `job_events` rows every
0.4 s), with a heartbeat every 30 s. Clients de-duplicate by event `id`, so a
reconnect is seamless.

```json
{"type": "snapshot", "data": {"project_id": "…", "job_id": "job_…", "kind": "render", "status": "running",
  "phase": "video", "progress": 0.6, "message": "Generating images + composing video", "recent_events": […]}}
{"type": "event", "data": {"id": 812, "phase": "storyboard", "status": "frame",
  "message": "Scene 2 drawn: The recipe", "progress": 0.73,
  "payload": {"scene_id": "scene_2", "preview_url": "/assets/…/scene_2.png"}}}
{"type": "heartbeat"}
```

| `phase` / `status` | When |
|---|---|
| `story` / `started`, `complete` | Script being written, then ready |
| `storyboard` / `script` | The script exists: payload has scenes, cast and image budget |
| `storyboard` / `frame` | One preview is drawn: payload has `scene_id`, `preview_url` |
| `storyboard` / `portrait` | One character's portrait is drawn: payload has `character_id`, `portrait_url`, `drawn` (false for a stand-in, which the render replaces) |
| `audio`, `video` / `started`, `complete` | Render phases |
| `audio`, `video` / `step` | A unit of render work finished (DST-38): payload `{step: voices\|portraits\|pictures\|shots\|cut, done, total}`, e.g. shot 7 of 18 |
| `edit` / `understood` | What a change was taken to mean: payload has `summary` |
| `edit` / `step` | Each edit step, in words ("Recording the voices") |
| `complete` / `complete` | Done: payload has the new `version` |
| `error` / `failed` | Failed: `message` says why |

## Example: a film from the command line

```bash
B=https://139-185-59-132.sslip.io
curl -c jar -H 'content-type: application/json' \
     -d '{"email":"me@example.com","password":"…"}' $B/api/auth/login
curl -b jar -H 'content-type: application/json' \
     -d '{"prompt":"A lighthouse keeper befriends a stranded whale","scene_count":3,"target_duration_s":24}' \
     $B/api/pipeline/plan
# → {"project_id":"20261004_…","job_id":"job_…","status":"queued","websocket":"/ws/progress/20261004_…"}
curl -b jar -X POST -H 'content-type: application/json' -d '{}' $B/api/pipeline/render/20261004_…
curl -b jar $B/api/pipeline/film/20261004_…
```
