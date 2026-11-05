# Dastango Studio: UI and UX design (DST-38)

**Status: design v7 of 5 Nov 2026, built in `web/` on 6 Nov 2026 (DST-38). The canvas stays the design source; section 7 holds the rules for changing it.** It answers the friction points in [USER_JOURNEYS.md](USER_JOURNEYS.md)
and Hashim's review (3 Nov) that the interface falls short of ChatGPT and Higgsfield (F22). The visual design is on a design
canvas (create, review, render, watch, a design system, a type comparison and two phone screens):
https://claude.ai/artifact/6an7trcrooCAGt2UnHfdQy

Four earlier drafts were rejected by Saad on 5 Nov as looking generated: a gradient draft, a Geist and amber draft, a version
with boxed selection and Fraunces type, and one with a warm brown-black, cream and amber palette. What follows is what was learned
from each rejection. In v5 the type became Chillax and Synonym (the files Saad downloaded) and the palette was re-derived from measurement. v6 added a light
mode, a new Review screen (section 4) and stopped reusing the logo's shapes as interface icons. v7 makes the light mode
terracotta minimalist (no white anywhere), removes the shadow from the Create background in both modes, adds a soft grain
and dust to the other screens, and draws every scenario in both modes as a clickable prototype (section 4).

## 1. What was studied, and how sure we are

Only products that share Dastango's job (a prompt becomes a film that is reviewed, rendered and edited) were kept.

| Source | How it was studied | What we took |
|---|---|---|
| **ImagineArt** (home and Film Studio) | Opened in Chrome on 5 Nov; computed styles read from the page | Near-black ground, one large prompt, scenes with shots inside them on a timeline, per-shot controls. Not taken: the left rail of tools (we have one workflow, not a suite) and the violet button. |
| **DaVinci Resolve** | Public product pages and documentation | Work in stages ("pages": cut, edit, colour, deliver), with the stage bar always visible. Became the four-step bar at the top and the render bar at the bottom. |
| **Descript** | Public product pages | The script is the editing surface. Became the script column that leads Review and Watch. |
| **Boords** | Public product pages | Script beside frames, shot lists, review links, version history. |
| **Storyboard tools** (Boords, StudioBinder, MockFlow, Higgsfield's shot-list guidance) | Vendor and comparison pages found by search, not their screens | Numbered frames in rows of three or four with the script as the caption; a shot needs number, picture, description, length and camera movement. Inline editing works best when the editable part becomes a slightly raised card (Salesforce's design team), and an open inspector can crowd the canvas (a WordPress Gutenberg discussion). The Review screen follows these. |
| **Runway** | A third-party extraction of its design system (not official) | Nearly invisible chrome: hairlines, no shadows or gradients, the pictures supply the colour. |

Limits: the browser could open ImagineArt only. The rest is from public text, not screens, and nothing here measures how any
product feels to use. Higgsfield, LTX Studio and Google Flow were read about in the first round, but their screens could not be
opened, so they are not design sources here.

## 2. What makes an interface look generated, and what we did about it

From design write-ups and the three rejected drafts. These are tendencies, not rules, and the sources are opinion.

| Tell | Our answer |
|---|---|
| Default faces (Inter, Geist, Fraunces, Outfit-style geometric sans) | Chillax and Synonym, chosen by Saad, one weight each. Fraunces appears on lists of faces that mark a page as generated, and Saad objected to it directly. |
| Purple or neon, or orange on black for every control | One accent, the brand's ember, used only for the live edge (the current stage, progress, the render button) and measured at under 1% of the screen. Selection is a ring and a check; everyday buttons take the text colour (cream in dark mode, umber in light). |
| Glass cards, nested bordered cards, pills for every status | Unboxed frames and hairline lists. No pills for steps, status or filters. |
| Selection by tinted box, coloured side border or inverted pill | A solid ring around a selected picture, a check and full brightness on a selected row or option, and the word "Editing" on the item being edited. It never relies on colour alone (WCAG 1.4.1) and carries `aria-current`. |
| Small all-caps mono labels and sentence-long helper text under everything | Sentence case only. A label stays only if it carries information; a line that restates the layout is cut. |
| A stock countdown ("about two minutes left") | The headline names the work: "Cutting scene 4, The Climb." Time appears as a measured range ("usually five to six minutes"), not a made-up countdown. |
| Generic background effects (gradient mesh, particles, glow) | Real frames from real films, slowly doing the camera moves the renderer actually makes, kept clear of the text (section 3). |

What gives it a point of view: the dastango told stories by lamplight from an arched niche, and the logo is that arch with a flame.
The mark is kept for the brand alone: it appears in the top bar (and the favicon) and nowhere else. Earlier drafts reused the flame
as a selection mark and the arch as step icons and frame shapes, which made the logo do double duty and did not sit right, so the
interface uses plain shapes (dots, rings, checks, round avatars) and the point of view comes from the type, the neutral surround,
the scarce ember and the real film frames.

## 3. System

**Palette: two modes.** Both were chosen from the research. **Dark** is graphite: near-neutral, because people here judge the colour of
generated frames and ask for them warmer or darker, and DaVinci Resolve offers a gray background for the same reason (a tinted
surround biases how colour is read; the source is a forum discussion, so it is a rationale, not a proof). **Light** is terracotta
minimalist: a sand ground, surfaces tinted like fired clay and lime plaster, umber text, and nothing white (the first light draft used
cream and near-white and looked washed out). Both ramps sit at very low chroma, surfaces get lighter as they rise, and neither uses pure
black or pure white. Contrast is measured against each mode's ground. The tokens are CSS variables, with the light set under a
`theme-light` class.

| Token | Dark | Light | Contrast (dark, light) |
|---|---|---|---|
| Ground | `#0c0d0f` | `#ece1d3` | |
| Surface, raised | `#151618`, `#1f2022` | `#f4ebe0`, `#f8f2e9` | |
| Text | `#ebedf1` | `#352118` | 16.6:1, 11.8:1 |
| Muted | `#b9babd` | `#5d483c` | 10.0:1, 6.6:1 |
| Faint | `#97989b` | `#705e52` | 6.7:1, 4.8:1 (production's `#6f6960` is 3.6:1 and fails) |
| Ember (the logo, the render button in dark mode) | `#d98b3a` | `#d98b3a` (logo and progress only; buttons are umber) | 7.1:1 label on it in dark |
| Ember as text or a small mark | `#d98b3a` | `#923d17` (terracotta ink) | 7.1:1, 5.6:1 (ember on sand is only 2.1:1) |
| Problem | `#f35c49` | `#af2b25` | 6.0:1, 5.1:1 |
| Ready | `#67cfa5` | `#00694a` | 10.2:1, 5.2:1 |
| Keyboard focus ring | `#8db0fb` | `#355ab4` | 9.0:1, 5.0:1 |

**The ratio.** About 90% neutral surface and text, about 8% structure (hairlines, muted text, icons) and no more than 2% accent.
Product-design guidance for tools argues for a much smaller accent than the 60-30-10 rule used on brand pages, and in a tool that
edits colour the accent has to be scarce so the frames carry the colour. Measured in dark mode with the pictures hidden, ember is
0.48% of the new Review screen and 0.15 to 0.17% of Create, Render and Watch.

**Why ember.** The logo's flame is ember in production and changing it is a brand decision, so the accent is the brand's own and is
kept scarce. The canvas shows the same button in vermilion, lapis and malachite if Saad wants the logo and accent to move together.
The warm brown-black with cream and amber of drafts one to four is a very common generated-interface look, which is what moved
the surround to graphite.

**Type.** Two faces from Fontshare, supplied by Saad in `FontshareKit-2611000575/` (untracked). The ITF Free Font License allows
commercial use and self-hosting, and forbids modifying the files, including subsetting and format conversion, so the app ships the
supplied `.woff2` files as they are and keeps the license text beside them.

| Face | Role |
|---|---|
| Chillax Semibold (600) | Every heading, large and small: the question on Create, the render headline, film, scene and shot titles, speaker names, group labels |
| Synonym Regular (400) | Everything read or pressed: script, buttons, status, timecodes |

Each face ships in one weight only, so there is no italic and no bold anywhere; emphasis comes from size, colour and which face it is.
Two limits to design around: Synonym's digits are not tabular (a ticking timecode would shift sideways, so time readouts sit in a
fixed-width box), and neither face has an arrow glyph (arrows are drawn as icons).

**Shapes and motion.** Plain shapes only: round avatars, dots for status and steps, rings and checks for selection, and the
camera-move glyphs (hold, sway, push in, pan, pull out), which describe the move itself and are not the logo.

**The Create background.** Create and the signed-out screen show three real frames, each doing a move the renderer makes (push in, pan,
pull out), over 42 seconds. There is no overlay at all in either mode: no vignette and no shadow, because both read as a box or a fog.
In dark mode the frames fill the page at a flat 24% opacity. In light mode a flat low opacity looked like a white veil, so the frames
are blended onto the sand with multiply instead: they keep their own warm colour, cover the top of the page and fade into the ground.
Because dark text needs a light backdrop, only large or full-strength text sits over the picture in light mode (the greeting, the
headline and, on sign-in, the lede); the prompt box, the idea list and the sign-in form are solid surfaces. The loop starts and ends on a
visible frame. Measured on the rendered page at three points of the loop: dark mode gives 8.0:1 for body text and 4.9:1 for muted text
over the brightest pixels; light mode gives 4.7 to 4.9:1 for umber text over the darkest pixels (headline, greeting and the sign-in
lede), and 10:1 for the phone's hint line. Nothing small and faint sits over the picture. Under `prefers-reduced-motion` it holds one
still frame.

**The other screens.** Behind the content there is a soft film grain (7% in dark mode, 10% in light) and a few slowly drifting dust
specks (30% and 32%), drawn in CSS with no image files. They sit behind everything, never over text, and stand still under reduced
motion.

Each board's styles carry their own class prefix, because the boards on the canvas can share one page and a shared class name such as
`.stage` let one board's rules override another's.

## 4. Screens and the friction they answer

| Screen | What it does | Friction (USER_JOURNEYS.md) |
|---|---|---|
| **Create** | Centred prompt box (length, scenes and the button inside it) over slowly moving frames; length and scene count with a link that explains a scene; the time a plan takes; three example ideas; "Your films" with status, length and date | F10, F12 |
| **Review** | A storyboard sheet: each scene is a row with its number, title, tone and length, and three or four shot cards, each with its frame, shot name, camera move, length and the line spoken under it (the script is the caption, as in storyboard tools). Every shot has its own "Edit shot" action. The selected shot gets a ring and says "Editing", and its editor opens in place under that scene (a slightly raised card with a preview, a timing strip, camera move, framing and "Redraw this picture"), so there is no fixed inspector column. Scenes already checked collapse to one line. The cast sits at the top; the bar at the bottom shows the totals, the cost before the button ("about 5 minutes to render, 0 more pictures") and one ember button, "Render this film" | F4, F16 |
| **Render** | Names the scene being cut; a grid where each scene is marked as it is cut; a ledger of finished steps with times; a plain note when a picture is a stand-in, with "Redraw it"; "you can leave, it will wait in Films" | F3, F8, F13 |
| **Watch** | Script column that seeks the player; versions as a ledger with the current one marked "Playing"; one sentence to change something; Download | F15, F17 |
| **Phone** | Create and Render at 390 px with a bottom tab bar | |

### Every scenario, in dark and light

The canvas is a clickable prototype: nineteen boards, each in dark and in light, joined by real links. Open one and press Play. A thin bar
at the top of each board steps through the scenarios in order, jumps to the index, and switches the mode. Buttons and links inside a
screen go where they would in the product (Make the storyboard opens Planning, Edit shot opens the editor, Render this film opens
Render, Make the change opens the change in progress).

| # | Scenario | What it shows |
|---|---|---|
| 1 | Signed out | An example film and the sentence that made it, beside the sign-in (F11) |
| 2 to 4 | Create: the prompt, what is a scene, the idea is too short | The prompt box, the scene explainer (F10), a plain error with the box outlined |
| 5 | Planning | The script is written and the frames are drawn one by one, with time and what is still free to change |
| 6 to 8 | Review: the shots, editing a shot, redrawing a picture | The storyboard sheet; the editor opening in place; a picture being redrawn that can be stopped (F4) |
| 9, 10 | Render: in progress, stopped | The scene being cut, stand-in note (F3, F8); what was saved and Try again (F13) |
| 11 to 13 | Watch: playing, a change being made, the change was not understood | Versions (F15); what was understood and progress (F17); nothing changed, with things to try |
| 14, 15 | Films: your films, the first time | Status, length, date, search and filters (F12); the empty state |
| 16 to 19 | Phone: create, review, render, watch | The same flow in one column |

## 5. What building it needs

Front end only unless marked. Order is by value to the creator, not by size.

1. Tokens and faces in `web/app/globals.css` and `web/lib/fonts.ts` (`next/font/local` with the supplied `.woff2` files and the license text; the kit folder itself is not committed as it is); drop the all-caps `.label` style; move the neutrals and `--faint` to the table above (`#6f6960` fails contrast); keep `--ember`.
2. App shell: top bar with the production logo and the four-step stepper; the shared selection pattern (ring, check, word) as one component; the theme tokens above with a light and a dark set, following the system setting with a toggle in the account menu; the grain and dust layer as one CSS-only component behind every screen except Create and sign-in.
3. Render screen: stages, the real step times from the progress events, "you can leave this page" (F3), stand-in note (F8) and "Try again" on a failed render (F13). The progress events already carry stage and scene; the typical-time range is a constant from our measurements.
4. Create: centred layout, the moving frames (three stills served from the user's own latest films, or three shipped examples when there are none), scene explainer (F10), example film beside sign-in (F11).
5. Films list: status, length and date (F12); version numbers in Watch (F15); progress text while an edit runs (F17).
6. **Shot editor (F4)** is the only part that needs the back end: an API to store a per-shot override (camera move, framing) and re-render one scene without redrawing the others. It is last because it is the largest change.

Not covered here and still open from the review: F18 (edit one object without redrawing the scene) needs an image-edit provider
we do not pay for; F19 (a wider range of post-render changes); F21 (render time on the 2-core VM, which needs a cost decision about a larger worker).

## 6. Open decisions

- Whether the accent stays the brand's ember (recommended: no change to the production logo) or moves with the logo to vermilion, lapis or malachite.
- Whether the product follows the system's light or dark setting by default (recommended) or starts in one mode.
- Whether the moving frames on Create come from the user's own films once they have made some.
- Whether the three Fontshare files go into the repository under `web/` (the license allows self-hosting; copying them in is a repository decision).
- Build order: the list in section 5 is the recommendation.

## 7. Rules for every future change (the canvas and the code)

The canvas is the design source and `web/` is its implementation; they are changed together. Before changing either, follow these.

**Where things live.** Canvas: the link at the top. Its sources (the build scripts that produce every board in both modes, the
palette and contrast checks) are backed up outside the repository in `DST-38-evidence/design-canvas-v7-final`. The code's tokens are
`web/app/globals.css` (names are the same as the System board's), the faces `web/lib/fonts.ts`, the shared pieces
`web/components/` (`TopBar`, `Dust`, `FilmGrid`) and `web/components/studio/` (`Sheet`, `Dock`, `RenderProgress`).

**Rules**
1. **Two modes, one set of names.** Every colour is a token; never a hex in a component. A screen is designed and checked in both
   modes. Light is terracotta minimalist: no white, anywhere.
2. **Contrast is measured, not judged.** Text 4.5:1 or better (large text 3:1), measured against the real background. The accent
   (ember) is never thin text on the sand (it measures 2.1:1): text and dots on the ground use `--ac-t`.
3. **The accent is scarce.** Ember is the logo, live marks (progress, the working dot) and, in dark mode only, the one action that
   costs time. In light mode that action is umber, like the text (`.btn.ember`). A second accent colour is a decision, not a tweak.
4. **Selection is never colour alone.** A ring, a check and the word ("Editing", "Playing", "Checked"). Status is a dot with a word
   beside it. The logo's flame and arch appear only in the top bar and are never reused as an icon.
5. **Type.** Chillax Semibold for every heading, Synonym Regular for everything else; no italic, no bold (the kit has neither and
   `font-synthesis` is off). The files are supplied unmodified, with their licence beside them; do not subset or convert them.
6. **Create and sign-in carry the moving pictures** at a flat strength with no overlay, vignette or shadow; everything read there sits on
   a solid surface. Every other screen gets the grain and dust layer.
7. **Say only what is measured.** Times and counts on a screen come from events or from `docs/USER_JOURNEYS.md`; "usually 5 to 7
   minutes" is a stated range from measured renders, never a countdown. What cannot be known is not claimed (the render screen does
   not say "scene 4 of 6": shots of all scenes render together).
8. **Every control states its cost** (images to draw, "nothing is drawn", "cut again") before it is pressed.
9. **New screens go on the canvas first**, in both modes, with their states (empty, working, failed), then into code.

## 8. Built (DST-38, 6 Nov 2026)

Tokens, both faces and the theme (system by default, Auto/Light/Dark in the account menu); the app bar with the four stages; Create
with the moving pictures, a scene explainer (F10) and your films; sign-in with an example film beside it (F11); Films with state, length
and date, filters and search (F12); Review as a storyboard sheet with the shot editor (F4); the render screen with real step counts,
how long such a film usually takes and "safe to leave" (F3), and "Try again" with the reason when it stops (F13); Watch with version
numbers from 1 (F15), a stand-in picture note with a redraw button (F8) and what an edit is doing (F17); the engineer's voice note is gone
from the render bar (F16). Needing the back end: step events, the per-shot camera (stored on the scene, re-cutting only the scene that
changed), the stand-in marker, `number` and `cut`, and the library's `working` state.
