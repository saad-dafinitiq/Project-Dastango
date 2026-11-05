# User journeys and friction points

**Status: draft of 5 Nov 2026 (DST-37).** Mapped from the code at `6c7bcb4` (DST-36 merged) and from
competitors' public pages, then **walked live on production on 5 Nov 2026** (signed in as the demo account, one
film, 30 s and 3 scenes, from the first sentence to a version change and a go-back). Steps the walk did not cover
(the signed-out screens, failures, phones) stay marked **[WALK]** and must not be read as measured.

How each statement is labelled:

| Tag | Meaning |
|---|---|
| **[Code]** | Read from the source of the web app or API at the commit above. It says what the product does, not how it feels. |
| **[Measured]** | A number from a run recorded in `CLAUDE.md` or `docs/EVAL.md`, with its date. |
| **[Public]** | From a competitor's own public page, read on 5 Nov 2026. Marketing pages, so "not stated" is common. |
| **[Walked]** | Seen or timed on the live site on 5 Nov 2026 (screenshots kept). |
| **[WALK]** | Still needs the walk of the live site. |
| **[Hypothesis]** | A likely problem, to be confirmed or dropped by the walk. |

---

## 1. Who this is for

Dastango makes a short film from one sentence, and lets the creator review the storyboard before the expensive
render, then change the film by typing a sentence and go back to any earlier version. The two people this
document follows:

- **The first-time creator** wants to see a film of their idea quickly and decide whether this is worth
  their time. They know nothing about scenes, storyboards or versions.
- **The returning creator** wants yesterday's film back, to fix one thing without losing what is good,
  and to trust that undo works.

**Moments of truth**, where a creator decides to stay or leave:
1. The first sight of the product, before sign-up: do I understand what I get?
2. The wait for the storyboard and the render: is it working, and how long?
3. The first change by sentence: did it do what I meant, and can I undo it?

---

## 2. Journey 1: the first-time creator

Landing, sign in, write a prompt, wait for the storyboard, review and edit, render, watch, download.
Walked 5 Nov 2026 with the prompt "A clockmaker in a flooded city repairs the hours people lose", 30 s, 3 scenes.

| # | Step | What the creator sees | What they must understand or decide | Wait | What can go wrong | Friction |
|---|---|---|---|---|---|---|
| 1 | Land | **[Code]** The page's static text is one line: "Dastango: a short film from one sentence." Everything else is drawn after the page loads, so a visitor who is not signed in meets the sign-in screen, with a link "New here? Read how it works". **[WALK]** the signed-out screen was not looked at. | Is this for me? Is it free? | **[WALK]** | A visitor sees no example film or sample result before being asked for an account (from the code). | F11 |
| 2 | Sign in or make an account | **[Code]** "Welcome back" with email and password, and GitHub sign-in if the server has it. "Make an account" appears only when sign-ups are on; the first person on a fresh server makes the admin account. | Which button is mine; whether I may sign up at all. | **[WALK]** | Sign-ups are **closed by default** (`ALLOW_SIGNUPS`), so a new creator has no way in unless someone makes them an account. There is no password reset and no email check. | F1, F2 |
| 3 | Write a prompt | **[Walked]** Once signed in, the first screen is the composer: "What happens in your film?", a box "A sentence is enough.", length 20/30/45/60 s (30 s selected), scenes (3), four example prompts, and under it the note "You'll see every scene, its lines and a first frame before anything is rendered. 3 images to plan, and nothing more until you say so." Below it "Your films" (9 for this account). The button is "Write the storyboard". | What a "scene" is; how length and scenes trade off. | Instant: pressing the button opens the film's own page at once | An empty prompt cannot be sent. | F10 |
| 4 | Wait for the storyboard | **[Walked]** "Writing the script · 6 s", then the title and tone appear and "Drawing the storyboard · 16 s" with the cast cards showing "drawing", then the three scene cards with their pictures, shots strip and lines. | Nothing; just wait. | **About 35 s** from pressing the button to a complete storyboard (script text by about 10 s, cast and frames by about 35 s) | Image providers can be at their limit; a scene falls back to a placeholder. Not seen in this run: the render bar read "rendering draws 0 more images". | F8 |
| 5 | Review and edit | **[Walked]** A cast row (Narrator, Eli, Mira, each with a portrait and voice), three scene cards, each with a picture, a strip of three shots (for example "WIDE pan right", "MEDIUM pan left", "ELI pull out"), the scene's tone, and its lines with the voice that speaks each. An "Edit" button sits on each card. Below: **Voices** ("Kokoro" with "Hear it"), **Subtitles** (English), and the render button. Beside the render button is the note "Open-source (Apache-2.0), runs offline on the CPU. No key, no GPU." | Whether to edit now or render and fix later; what the shots strip means. | A text edit is instant; a redraw takes seconds **[WALK]** | The creator cannot change a shot's camera move, framing or length, only read it. The note about the voice engine is written for engineers. | F4, F16 |
| 6 | Render | **[Walked]** A line under the title: "Recording the voices · 6 s", then from about 30 s "Drawing the shots and cutting the film", with the elapsed seconds and a Stop button. The same message stayed for the next 4 to 5 minutes. No estimate, no step count. | That it will take minutes and it is safe to wait or leave. | **A 29 s film took between 301 s and about 350 s on the live server** (the counter read 301 s at my last reading and the film was ready by the next, 50 s later). Voices took under 35 s of that. | A render can fail ("This one didn't make it", with the reason; **[WALK]** not seen). **[Code]** The status line has no retry control; the creator must find "Render this film" again. | F3, F13 |
| 7 | Watch | **[Walked]** "Your film is ready" (no time or size), a player with the film at 1920x1080, 24 fps, "v2", the scenes as chapters with a picture and start time (0:00 Rising Tide, 0:10 Lost Hours, 0:19 Restored Time), subtitles burned in. | Nothing. | Instant | **[WALK]** on phones and other browsers | F14 |
| 8 | Keep or share | **[Walked]** A **Download** button and "Back to the storyboard". There is no share link and no copy-link. | How to show someone else. | The download is a file of about 21 to 27 MB for a 63 s film **[Measured, DST-35, DST-36]** | The creator can send only a file. | F7 |

Machine time from pressing "Write the storyboard" to a watched film, for a 30 s, 3-scene film: **about 6 minutes**
(about 35 s of storyboard and 5 to 6 minutes of render), plus however long the creator spends reviewing. The only long
part is the render, and it is also the part with the least feedback.

---

## 3. Journey 2: the returning creator

Sign in, find my films, open one, edit it (on the storyboard or after the render), re-render, compare versions.
The edit and version steps were walked on the film above.

| # | Step | What the creator sees | What they must understand or decide | Wait | What can go wrong | Friction |
|---|---|---|---|---|---|---|
| 1 | Sign in | **[Code]** The same sign-in screen. | The password they chose. | **[WALK]** | A forgotten password cannot be reset by the creator. | F2 |
| 2 | Find my films | **[Walked]** "Your films" under the composer on the first screen, as a grid of posters, a "storyboard" badge on a film never rendered. **[Code]** The empty state reads "Nothing yet. Your first film will appear here." | Which of my films is which. | Instant | **[Code]** A film with no frame shows "no frame yet". The grid shows no date or length and has no search **[Hypothesis]**. | F12 |
| 3 | Open a film | **[Walked]** Pressing the prompt button opened the film's own page at once. **[Code]** A rendered film opens on the player; an unrendered one on its storyboard. | Whether it is a finished film or a draft. | Instant | **[WALK]** | |
| 4 | Edit | **[Walked]** After the render: "Change something", a box "Say what to change: ..." and four suggestions made from this film ("Make scene 2 darker", "Make Eli's voice warmer", "Add tense background music", "Apply the noir filter"). Choosing "Make scene 2 darker" and pressing "Make the change" showed at once what it understood ("a darker look · scene 2"), then "Regrading the pictures" with elapsed seconds and Stop, then "Change made". | What the change can do; the phrasing that works. | **Between 95 s and 135 s** for a colour change on one scene of a 29 s film | **[Code]** A sentence it cannot carry out is refused with "Nothing was changed. The film is as it was." and what to say instead (not seen). The change **is applied** and reported as it starts: there is no "apply this?" before it, but Stop exists. | F5 |
| 5 | Re-render | **[Walked]** The film updated in place and became "v3". | That the old film is kept as a version. | As above | **[Code]** A film far from its target length is finished with a warning in the status line. | |
| 6 | Compare versions | **[Walked]** A list of versions in the creator's own words: v3 "Make scene 2 darker" (with what it understood, and how long ago), v2 "First cut", with "Playing" on the current one and "Go back" on the others. Pressing "Go back" on v2 made **v4 "Back to version 2"**, and the film reloaded. | Which version is which; what changed between two. | **Under 10 s** (the player was blank for about 3 s, ready at about 8 s) | There is no side by side: to compare, the creator goes back, watches, and goes forward. The first film's first version is called **v2**: v1 is the storyboard, which is not shown. | F6, F15 |

---

## 4. Friction register

Impact is on the creator (**H**igh, **M**edium, **L**ow); effort is for us (**S**mall: under a day, **M**edium: a few
days, **L**arge: a week or more). "Evidence" says how sure we are.

| ID | Where | Friction | Evidence | Impact | Effort |
|---|---|---|---|---|---|
| F1 | J1 step 2 | A new creator cannot sign up: sign-ups are off by default and there is no invitation or waitlist. | [Code] | **H** | **S** (open them) to **M** (invitation link). Needs a decision on cost and abuse, since each film uses the image quota. |
| F2 | J1 step 2, J2 step 1 | No password reset and no email check. | [Code], known (ROADMAP) | **M** | **L** (needs a mail service; the project is at $0) |
| F3 | J1 step 6 | A render takes minutes and the only feedback is one line and elapsed seconds: no stage count, no typical time, no "safe to leave this page". **Walked: the same message, "Drawing the shots and cutting the film", stayed on screen for 4 to 5 minutes** while a 29 s film rendered (301 to about 350 s in all). The job is durable and a reload replays progress, but nothing says so. | [Walked], [Code] | **H** | **S** |
| F4 | J1 step 5 | The shot strip shows each shot's framing and move, but the creator cannot change them. A competitor (ImagineArt) offers a per-shot movement and a timeline. | [Code], [Public] | **H** | **M** to **L** (per-shot overrides stored with the scene, and a one-scene re-render) |
| F5 | J2 step 4 | A change by sentence is applied straight away and explained afterwards; there is no "this is what I will do, apply?" step. Undo exists but is a second action. | [Code] | **M** | **M** |
| F6 | J2 step 6 | Versions have no side-by-side compare. | [Code] | **M** | **M** |
| F7 | J1 step 8 | The only way out is a file download: no share link. | [Code] | **M** | **M** (a signed read-only link) |
| F8 | J1 step 4 and 6 | When every image provider is at its limit the film finishes with placeholder pictures. Not seen in the walk (every picture was drawn), so whether the screen tells the creator is still **[WALK]**. | [Code], ROADMAP | **H** if silent | **S** |
| F9 | J1 step 5 | The shot strip's cut times are estimates until the voices exist, and its medium shots are drawn by cropping in the browser. | [Code] | **L** | **S** |
| F10 | J1 step 3 | "Scenes" and "Length" are asked before the creator knows what they mean; the note about images is the only explanation. | [Code] | **M** | **S** |
| F11 | J1 step 1 | A visitor who is not signed in sees a sign-in gate and no example of the result, though the repository has a demo animation. Competitors show the product on the first screen (Media.io: the tool itself; ImagineArt: "Start New Project"). | [Code], [Public] | **H** | **S** |
| F12 | J2 step 2 | The film grid shows no date or length and has no search. | [Hypothesis] | **L** | **S** |
| F13 | J1 step 6 | A failed render says "This one didn't make it" with a reason, but the status line has no "Try again" control. | [Code] | **M** | **S** |
| F14 | J1 step 7 | The experience on a phone. | **[WALK]** | **?** | **?** |
| F15 | J2 step 6 | Version numbers start at v2: the first film is "v2 First cut" because v1 is the storyboard, which the creator never sees. | [Walked] | **L** | **S** |
| F16 | J1 step 5 | The render bar carries "Open-source (Apache-2.0), runs offline on the CPU. No key, no GPU.", a note about the voice engine written for engineers, next to the button the creator is about to press. | [Walked] | **L** to **M** | **S** |
| F17 | J2 step 4 | A change by sentence takes 95 to 135 s even for a colour change on one scene, and the only text is "Regrading the pictures" with elapsed seconds. What it understood is shown at once (a strength). | [Walked] | **M** | **S** (say what is happening and about how long) to **M** (faster) |

**Added after Hashim's review of 5 Nov** (his concerns, taken as given; the evidence column says "Review" where we have not measured them):

| ID | Where | Friction | Evidence | Impact | Effort |
|---|---|---|---|---|---|
| F18 | J2 step 4 | A creator cannot change one object in a picture (a character's coat, a prop) without the scene being drawn again. | Review | **H** | **L**. Needs an image-editing model; the free providers do not offer one, so it needs a paid key and a decision. |
| F19 | J2 step 4 | The range of changes after a render is narrow (colour, voice, speed, scene text). Creators will ask for more. | Review | **M** to **H** | **M** to **L** |
| F20 | J1 step 7 | The picture quality of the DST-35 (1080p) and DST-36 (camera coverage) films has not been judged by anyone but the team. | Review | **H** | **S** (watch and judge; no code) |
| F21 | J1 step 6 | Renders take about 10 to 12 s per second of film on the 2-core Oracle VM (5 to 6 minutes for 30 s), against about 6.7 s measured in M9. | [Walked], Review | **H** | **M**. A larger render machine (for example an AWS worker) costs money, so it needs a decision. |
| F22 | all | The look and feel falls short of ChatGPT and Higgsfield. | Review | **H** | **M** to **L**. This ticket's visual pass: [UI_DESIGN.md](UI_DESIGN.md). |

---

## 5. Compared with others

Read on 5 Nov 2026 from the products' own public pages, which are marketing pages: they say what the product
offers, not how it feels, so "not stated" is common. Nothing here is hands-on. ImagineArt and Media.io first,
as asked.

| | First screen | Control over shots | Waiting | Editing afterwards | Free tier and limits |
|---|---|---|---|---|---|
| **ImagineArt** (Film Studio) | "Start New Project"; a director-style brief with camera body, lens, focal length, genre and movement | Storyboard mode generates a connected sequence in one go; "Scene Jump" turns one shot into four; each shot's movement is set separately and shots sit on a timeline; characters, props and locations can be pinned and reused | Not stated | Per-shot movement and order; text and audio not stated | "Try it free"; amounts not stated |
| **Media.io** (Text to Video) | One screen under Create > Video AI: tabs for Reference, Image and Text to Video; a prompt box (8,000 characters); 5 s and 480P by default; a "Generate" button and a "Generate List" | None stated | Not stated | "Edit Video" and "Motion Control" appear in the navigation, undescribed | "Free, Unlimited & No Watermark" headline, unexplained; credits show 0 for a visitor; free creations are kept 7 days |
| **Higgsfield** (Popcorn storyboard) | A prompt or up to four reference images; Manual mode (describe each frame) or Auto mode (one prompt becomes 4, 6 or 8 frames); an aspect ratio | Close-up, medium and wide are set by wording the prompt, not by controls | Not described | Approve the storyboard, then export to Sora 2 | Free daily credits |
| **ChatGPT** | A chat box | Conversational | A streamed reply | Iterate by asking | **From general knowledge of the product, not checked today** |

Sources: [ImagineArt Film Studio](https://imagine.art/ai-film-studio), [Media.io text to video](https://www.media.io/ai/text-to-video/prompt-to-video-generator),
[Higgsfield Popcorn](https://higgsfield.ai/blog/The-AI-Storyboard-Generator-That-Feels-Like-Directing).

**What to learn from them**
- **Control.** ImagineArt treats each shot as something the creator directs. Ours is readable but not editable (F4).
- **First value.** Media.io shows the working tool on one screen. We show a gate (F11).
- **Their unknowns are our openings.** None of the three public pages states how long a generation takes, or how a
  creator reviews and undoes. We have a storyboard to review before any cost is spent, a "what it understood" line for
  every change, and versions that cannot lose work. These are strengths to show, not to hide.

---

## 6. What to fix first

Chosen for impact first and then small effort, for the next UX ticket. The first seven are small; F4 is larger and is
listed because it is the biggest gap against the nearest competitor.

| Rank | Fix | Why |
|---|---|---|
| 1 | **F3** Say what a render is doing: a stage count (voices, pictures, cutting), a typical time from our own measurements (about 5 to 6 minutes for a 30 s film now), and "you can leave this page, your film keeps rendering". | The longest wait is the product's biggest moment of doubt: walked, one unchanging message for 4 to 5 minutes. The data to answer it exists. |
| 2 | **F11** Show the product to a visitor: an example film and the sentence that made it, beside the sign-in. | The first moment of truth; competitors show the tool at once. |
| 3 | **F1** Decide how a new creator gets in (open sign-up for the beta, or an invitation link). | A creator who cannot sign up never reaches anything else. Needs a decision on cost and abuse. |
| 4 | **F8** Tell the creator when pictures could not be drawn, and offer to redraw them. | A silent placeholder looks like a broken film. Confirm what the screen says now. |
| 5 | **F10** Explain "scene" and "length" where they are asked, and keep 30 s and 3 scenes as the start. | Cheap, and it removes a decision a beginner cannot make. |
| 6 | **F13** On a failed render offer "Try again" next to the reason. | The reason is shown but the next step is left to the creator. Confirm in the walk. |
| 7 | **F16** Replace the engineer's note beside the render button with one line a creator understands (or drop it). | Seen on the live screen; costs minutes. |
| 8 (larger) | **F4** Let the creator edit each shot's move, framing and length in the strip. | The biggest gap against ImagineArt, and what the DST-36 review asked for. |

**Backlog:** F2 password reset, F5 apply-or-cancel before a change, F6 compare versions, F7 share link, F9, F12, F14, F15, F17.

### 6a. Updated order after Hashim's review (5 Nov)

| When | Items |
|---|---|
| **Now (DST-38, built 6 Nov 2026, see CLAUDE.md)** | F3 render stages, typical time and "you can leave this page" · F16 replace the engineer's note with the render button's own line · F10 explain scenes and length · F13 "Try again" on a failed render · F11 an example film beside sign-in · F8 tell the creator when stand-in pictures were used · **F22** the visual pass, with F12, F15 and F17 folded in because the same screens change |
| **Next** | F18 and F19 (post-render changes) |
| **Judge** | F20 (watch the DST-35 and DST-36 films and decide) |
| **Needs Hashim's decision** | F1 (who may sign up) · F21 (a larger render machine and its cost) |
| **Later** | F4 per-shot controls (the largest part of this ticket's design; needs a back-end change), F2, F5, F6, F7, F9, F14 |

---

## 7. What this document does not yet know

- **Not walked:** the signed-out screens (landing and sign-in, steps 1 and 2 of Journey 1 and step 1 of Journey 2), what the screens show on
  a failure or when every image provider is at its limit, and the experience on a phone or another browser.
- **One film, one account, one browser, one prompt.** The numbers are one run each: a 29 s, 3-scene film rendered in 301 to about 350 s, a
  colour change took 95 to 135 s, a go-back under 10 s. They are real but not an average.
- **The exact end of the render and the edit** were read from a counter polled every 10 to 60 s, so each is a range. The server's job list
  has the exact figures.
- Anything about ChatGPT, ImagineArt, Media.io or Higgsfield beyond what their public pages state.
- How creators actually behave: there are no users yet to interview. These are the map and the first guesses, to be tested with a few real
  creators.

## 8. Live measurements of 5 Nov 2026 (production, `6c7bcb4`)

| What | Measured |
|---|---|
| Storyboard, 30 s and 3 scenes | About 35 s from pressing the button |
| Render, a 29 s film at 1920x1080 | 301 s to about 350 s (voices under 35 s of it) |
| For comparison, the live server in M9 (3 Oct, 720p, before DST-35 and DST-36) | 292 s for a 43.3 s film |
| Per second of film | About 10 to 12 s now, against about 6.7 s in M9: roughly 1.5 to 1.8 times slower, which is what 1080p and the new shot plan were expected to cost on this server |
| Colour change on one scene | 95 s to 135 s |
| Go back to the first cut | Under 10 s |
| Versions made | v2 first cut, v3 the change, v4 the go-back |
