/** Stacked changes (DST-40): what the creator has chosen but not yet applied.
 *
 *  A change to a finished film means cutting it again, which takes a while, so choosing one does not
 *  start that. It is added to a stack, shown at once on the screen as if it were made, and the whole
 *  stack is applied together when the creator presses Apply: one job, one version, the film cut once. */
import type { Card, FilmPlayback } from "@/lib/types";

import { FRAMING_NAMES, MOVE_NAMES } from "./shots";

export type Pending =
  | { id: string; kind: "op"; key: string; op: string; params: Record<string, unknown>; label: string }
  | { id: string; kind: "sentence"; key: string; query: string; label: string };

/** Two choices about the same thing are one choice: the later replaces the earlier. */
export function keyOf(op: string, p: Record<string, unknown>): string {
  switch (op) {
    case "set_transition":
      return `t:${p.scene}:${p.shot ?? 1}`;
    case "set_camera":
      return `c:${p.scene}:${p.shot_id}`;
    case "regenerate_shot":
      return `r:${p.scene}:${p.shot}`;
    case "set_music":
      return `m:${p.scene ?? "*"}`;
    case "set_volume":
      return `v:${p.target}`;
    case "set_ambience":
      return `a:${p.scene ?? "*"}`;
    case "set_effect":
      return `e:${p.scene ?? "*"}`;
    case "set_graphics":
      return "g";
    case "move_scene":
    case "remove_scene":
      return `s:${p.scene}`;
    case "set_voice":
      return `vo:${p.character}`;
    case "edit_line":
      return `l:${p.scene}:${p.line}`;
    default:
      return `${op}:${JSON.stringify(p)}`;
  }
}

/** A camera or titles choice adds to what was chosen before (a move, then a framing); the rest replace. */
function merged(op: string, before: Record<string, unknown>, now: Record<string, unknown>): Record<string, unknown> {
  if (op !== "set_camera" && op !== "set_graphics") return now;
  if (now.reset) return now;
  const { reset: _drop, ...rest } = before;
  void _drop;
  return { ...rest, ...now };
}

/** The stack with this change in it, replacing an earlier choice about the same thing. */
export function stacked(pending: Pending[], op: string, params: Record<string, unknown>, label = ""): Pending[] {
  const key = keyOf(op, params);
  const before = pending.find((p) => p.kind === "op" && p.key === key) as Extract<Pending, { kind: "op" }> | undefined;
  const next: Pending = {
    id: before?.id ?? `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    kind: "op",
    key,
    op,
    params: before ? merged(op, before.params, params) : params,
    label,
  };
  return before ? pending.map((p) => (p === before ? next : p)) : [...pending, next];
}

export const opItems = (pending: Pending[]) =>
  pending.filter((p): p is Extract<Pending, { kind: "op" }> => p.kind === "op").map((p) => ({ op: p.op, params: p.params }));

// ---- showing the stack as if it were already applied -----------------------------------------

const sceneOf = (p: Record<string, unknown>) => (p.scene === undefined || p.scene === null ? null : String(p.scene));
const same = (id: string, ref: string | null) => ref !== null && (id === ref || id === `scene_${ref}`);

/** The storyboard cards with every stacked change shown on them, and marked as pending. */
export function overlayCards(cards: Card[], pending: Pending[], speeds: Record<string, number>): Card[] {
  const ops = pending.filter((p): p is Extract<Pending, { kind: "op" }> => p.kind === "op");
  if (ops.length === 0) return cards;
  return cards.map((card) => {
    const mine = ops.filter((p) => same(card.scene_id, sceneOf(p.params)));
    if (mine.length === 0) return card;
    let staged: string | undefined;
    const shots = (card.shots ?? []).map((s, j) => {
      let shot = s;
      for (const p of mine) {
        const q = p.params;
        if (p.op === "set_transition" && j === Number(q.shot ?? 1) - 1 && shot.transition) {
          const kind = (q.transition as string | undefined) ?? shot.transition.kind;
          const ms = kind === "cut" ? 0 : (q.ms as number | undefined) ?? (q.speed ? speeds[q.speed as string] : undefined) ?? (shot.transition.ms || 400);
          shot = { ...shot, transition: { ...shot.transition, kind, ms, chosen: !q.reset }, pending: true };
        } else if (p.op === "set_camera" && q.shot_id === shot.shot_id) {
          const move = (q.move as string | undefined) ?? shot.move_id;
          shot = {
            ...shot,
            move_id: move,
            move: MOVE_NAMES[move] ?? shot.move,
            framing: ((q.framing as CardShotFraming | undefined) ?? shot.framing) as CardShotFraming,
            overridden: !q.reset,
            pending: true,
          };
        } else if (p.op === "regenerate_shot" && j === Number(q.shot) - 1) {
          shot = { ...shot, pending: true, redraw: true };
        }
      }
      return shot;
    });
    for (const p of mine) {
      if (p.op === "remove_scene") staged = "Removed when you apply";
      else if (p.op === "move_scene") staged = `Moves to place ${p.params.to} when you apply`;
    }
    return { ...card, shots, staged };
  });
}
type CardShotFraming = "wide" | "medium" | "close" | "cutaway";

/** The film's sound, titles and looks with every stacked change shown on them. */
export function overlayFilm(film: FilmPlayback, pending: Pending[]): FilmPlayback {
  const ops = pending.filter((p): p is Extract<Pending, { kind: "op" }> => p.kind === "op");
  if (ops.length === 0) return film;
  const sound = film.sound ? { ...film.sound, moods: { ...film.sound.moods }, scene_music: { ...film.sound.scene_music }, ambience: { ...film.sound.ambience } } : film.sound;
  const titles = film.titles ? { ...film.titles } : film.titles;
  const effects = { ...(film.effects ?? {}) };
  const ids = film.chapters.map((c) => c.scene_id);
  const target = (p: Record<string, unknown>) => {
    const s = sceneOf(p);
    return s === null ? ids : ids.filter((id) => same(id, s));
  };
  for (const { op, params: q } of ops) {
    if (op === "set_music" && sound) {
      const scoped = sceneOf(q) !== null;
      const [one] = target(q);
      if (q.reset) {
        sound.music_on = true;
        if (scoped && one) delete sound.scene_music[one];
        else (sound.music_track = null), (sound.scene_music = {});
      } else if (q.off) {
        if (scoped && one) sound.scene_music[one] = "none";
        else sound.music_on = false;
      } else if (q.track) {
        sound.music_on = true;
        if (scoped && one) sound.scene_music[one] = String(q.track);
        else (sound.music_track = String(q.track)), (sound.scene_music = {});
      } else if (q.mood) {
        sound.music_on = true;
        for (const id of target(q)) {
          sound.moods[id] = String(q.mood);
          delete sound.scene_music[id];
        }
        if (!scoped) sound.music_track = null;
      }
    } else if (op === "set_volume" && sound) {
      const pct = Number(q.percent);
      if (q.target === "music") sound.music_pct = pct;
      else if (q.target === "voices") sound.voices_pct = pct;
      else if (q.target === "ambience") sound.ambience_pct = pct;
    } else if (op === "set_ambience" && sound) {
      const key = sceneOf(q) === null ? "*" : target(q)[0];
      if (key) {
        if (q.reset) {
          if (key === "*") sound.ambience = {};
          else delete sound.ambience[key];
        } else if (key === "*") sound.ambience = { "*": String(q.kind) };
        else sound.ambience[key] = String(q.kind);
      }
    } else if (op === "set_graphics" && titles) {
      if (q.reset) Object.assign(titles, { title_card: false, scene_titles: false, lower_thirds: false, end_card: false, title_text: null, end_text: null, credits_text: null });
      else for (const k of ["title_card", "scene_titles", "lower_thirds", "end_card", "title_text", "end_text", "credits_text"] as const) if (k in q) (titles as Record<string, unknown>)[k] = q[k] === "" ? null : q[k];
    } else if (op === "set_effect") {
      for (const id of target(q)) {
        if (q.reset || q.effect === "none") delete effects[id];
        else effects[id] = { kind: String(q.effect), strength: String(q.strength ?? "normal") };
      }
    }
  }
  return { ...film, sound, titles, effects };
}

/** "Wide" or "Leo", for a shot, in the words a card uses. */
export const shotName = (framing: string, character: string | null) => (framing === "close" && character ? character : FRAMING_NAMES[framing] ?? framing);
