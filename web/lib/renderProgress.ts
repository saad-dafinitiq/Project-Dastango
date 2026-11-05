// What a render screen says, worked out from the job's real events.
//
// The pipeline reports each finished unit of work ("shot 7 of 18", shared/
// progress_hook.py), so every number here is read, not guessed. What cannot be
// known is which scene a shot belongs to (shots of all scenes render in one pool),
// so nothing here claims "scene 4 of 6".
import type { ProgressEvent } from "./types";

/** Seconds of rendering per second of film on the production VM, from the
 *  measured renders in docs/USER_JOURNEYS.md (F21: 10 to 12 s per film second,
 *  Nov 2026). Only used to say "usually", never as a countdown. */
export const RENDER_SECONDS_PER_FILM_SECOND = [9, 13] as const;

export type StageState = "done" | "now" | "wait" | "bad";

export type Stage = {
  id: "voices" | "pictures" | "shots" | "final";
  label: string;
  detail: string;
  state: StageState;
  /** Seconds the stage took (or has taken so far), from the server's timestamps. */
  seconds: number | null;
};

export type RenderView = {
  stages: Stage[];
  headline: string;
  /** Shots finished, for the progress bar; null before shots start. */
  shots: { done: number; total: number } | null;
  /** 0..1 across the stages, weighted by how long each usually takes. */
  fraction: number;
};

function ts(e: ProgressEvent | undefined): number | null {
  if (!e?.created_at) return null;
  const t = Date.parse(e.created_at.endsWith("Z") ? e.created_at : `${e.created_at}Z`);
  return Number.isNaN(t) ? null : t;
}

function lastStep(events: ProgressEvent[], step: string): ProgressEvent | undefined {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!;
    if (e.status === "step" && e.payload?.step === step) return e;
  }
  return undefined;
}

function firstStep(events: ProgressEvent[], step: string): ProgressEvent | undefined {
  return events.find((e) => e.status === "step" && e.payload?.step === step);
}

function counts(e: ProgressEvent | undefined): { done: number; total: number } | null {
  if (!e?.payload) return null;
  return { done: Number(e.payload.done ?? 0), total: Number(e.payload.total ?? 0) };
}

function span(a: number | null, b: number | null, now: number): number | null {
  if (a == null) return null;
  return Math.max(0, Math.round(((b ?? now) - a) / 1000));
}

/** `failed` marks the stage that was running when the job stopped. */
export function renderView(events: ProgressEvent[], now: number, failed: boolean): RenderView {
  const find = (phase: string, status: string) => events.find((e) => e.phase === phase && e.status === status);
  const audioStart = find("audio", "started");
  const audioEnd = find("audio", "complete");
  const videoStart = find("video", "started");
  const videoEnd = find("video", "complete");
  const shotsFirst = firstStep(events, "shots");
  const cutFirst = firstStep(events, "cut");

  const voices = counts(lastStep(events, "voices"));
  const portraits = counts(lastStep(events, "portraits"));
  const pictures = counts(lastStep(events, "pictures"));
  const shots = counts(lastStep(events, "shots"));
  const picDone = (portraits?.done ?? 0) + (pictures?.done ?? 0);
  const picTotal = (portraits?.total ?? 0) + (pictures?.total ?? 0);

  const picturesDone = Boolean(shotsFirst || cutFirst || videoEnd);
  const shotsDone = Boolean(cutFirst || videoEnd);
  const finalDone = Boolean(videoEnd);

  let current: Stage["id"] = "voices";
  if (audioEnd) current = "pictures";
  if (picturesDone) current = "shots";
  if (shotsDone) current = "final";

  const mk = (id: Stage["id"], label: string, done: boolean, detail: string, from: number | null, to: number | null): Stage => ({
    id,
    label,
    detail,
    state: done ? "done" : id === current ? (failed ? "bad" : "now") : "wait",
    seconds: done || id === current ? span(from, done ? to : null, now) : null,
  });

  const stages: Stage[] = [
    mk(
      "voices",
      audioEnd ? "Voices recorded" : "Recording the voices",
      Boolean(audioEnd),
      voices && voices.total ? `${voices.done} of ${voices.total} lines` : "",
      ts(audioStart),
      ts(audioEnd),
    ),
    mk(
      "pictures",
      picturesDone ? "Pictures drawn" : "Drawing the pictures",
      picturesDone,
      picTotal ? `${picDone} of ${picTotal}` : "",
      ts(videoStart),
      ts(shotsFirst ?? cutFirst ?? videoEnd),
    ),
    mk(
      "shots",
      shotsDone ? "Shots cut" : "Cutting the shots",
      shotsDone,
      shots && shots.total ? `${shots.done} of ${shots.total} shots` : "",
      ts(shotsFirst),
      ts(cutFirst ?? videoEnd),
    ),
    mk("final", "Final cut, subtitles and sound", finalDone, "", ts(cutFirst), ts(videoEnd)),
  ];

  let headline = "Getting ready";
  const cur = stages.find((s) => s.state === "now" || s.state === "bad");
  if (failed) headline = cur ? `Stopped while ${cur.label.toLowerCase()}` : "Stopped";
  else if (current === "voices") headline = "Recording the voices.";
  else if (current === "pictures") headline = "Drawing the pictures.";
  else if (current === "shots") headline = shots && shots.total ? `Cutting shot ${Math.min(shots.done + 1, shots.total)} of ${shots.total}.` : "Cutting the shots.";
  else headline = "Putting the film together.";

  // Weighted by where the time goes on the production VM: shots dominate.
  const weights = { voices: 0.1, pictures: 0.15, shots: 0.65, final: 0.1 } as const;
  let fraction = 0;
  for (const s of stages) {
    if (s.state === "done") fraction += weights[s.id];
    else if (s.state === "now") {
      const c = s.id === "voices" ? voices : s.id === "shots" ? shots : null;
      fraction += weights[s.id] * (c && c.total ? c.done / c.total : s.id === "pictures" && picTotal ? picDone / picTotal : 0);
    }
  }
  return { stages, headline, shots: shots && shots.total ? shots : null, fraction: Math.min(1, fraction) };
}

/** "5 to 7 minutes", from the film's length in seconds. */
export function typicalTime(filmSeconds: number): string {
  const [lo, hi] = RENDER_SECONDS_PER_FILM_SECOND;
  const a = Math.max(1, Math.round((filmSeconds * lo) / 60));
  const b = Math.max(a + 1, Math.round((filmSeconds * hi) / 60));
  return `${a} to ${b} minutes`;
}

export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
