import type { Film } from "./types";

export type FilmKind = "ready" | "working" | "draft";

const WORKING_WORDS: Record<string, string> = {
  plan: "Writing the storyboard",
  render: "Rendering",
  run_full: "Rendering",
  rerun_phase: "Rendering",
  edit: "Changing",
  shot: "Changing",
  op: "Changing",
  revert: "Going back",
};

export function kindOf(f: Film & { working?: string | null }): FilmKind {
  if (f.working) return "working";
  return f.stage === "rendered" ? "ready" : "draft";
}

/** "today", "yesterday", "2 Nov" from the server's naive UTC time. */
export function shortDate(iso: string | undefined, now = Date.now()): string {
  if (!iso) return "";
  const t = Date.parse(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(t)) return "";
  const d = new Date(t);
  const today = new Date(now);
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(today) - day(d)) / 86_400_000);
  if (diff <= 0) return "today";
  if (diff === 1) return "yesterday";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** "1:02" */
export function length(ms: number | null | undefined): string {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function statusLine(f: Film & { working?: string | null }, now = Date.now()): { kind: FilmKind; text: string } {
  const kind = kindOf(f);
  const when = shortDate(f.updated_at, now);
  if (kind === "working") return { kind, text: WORKING_WORDS[f.working ?? ""] ?? "Working" };
  const shape = f.format && f.format !== "16:9" ? f.format : "";
  if (kind === "ready") return { kind, text: ["Ready", length(f.duration_ms), shape, when].filter(Boolean).join(" · ") };
  return { kind, text: ["Storyboard", shape, when].filter(Boolean).join(" · ") };
}
