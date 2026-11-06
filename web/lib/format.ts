// The film's format and style (DST-34), as the interface names them. The numbers mirror
// shared/film_format.py; the back end is the authority, this only draws what it reports.

export type FormatId = "16:9" | "9:16" | "1:1";
export type StyleId = "realistic" | "anime";

export const FORMATS: { id: FormatId; audience: string; size: string; short: string }[] = [
  { id: "16:9", audience: "YouTube", size: "1920×1080", short: "YouTube" },
  { id: "9:16", audience: "Shorts, Reels, TikTok", size: "1080×1920", short: "Shorts" },
  { id: "1:1", audience: "Instagram feed", size: "1080×1080", short: "Instagram" },
];

export const STYLES: { id: StyleId; label: string; about: string }[] = [
  { id: "realistic", label: "Realistic", about: "Photographic, natural light" },
  { id: "anime", label: "Anime", about: "Painted, cel-shaded" },
];

/** CSS aspect-ratio value for a format: "9 / 16". Unknown formats are widescreen. */
export function ratio(format: string | undefined | null): string {
  return format === "9:16" ? "9 / 16" : format === "1:1" ? "1 / 1" : "16 / 9";
}

/** The variables every screen of a film reads: the frame's aspect ratio, as a number too (for
 *  "no taller than the window"), and how wide a card of it should be in a row of shots and tiles. */
export function formatVars(format: string | undefined | null): Record<string, string> {
  const tall = format === "9:16";
  const square = format === "1:1";
  return {
    "--ar": ratio(format),
    "--arn": tall ? "0.5625" : square ? "1" : "1.7778",
    "--col": tall ? "170px" : square ? "220px" : "240px",
    "--tile": tall ? "150px" : square ? "190px" : "220px",
  };
}

export function formatInfo(format: string | undefined | null) {
  return FORMATS.find((f) => f.id === format) ?? FORMATS[0]!;
}

export function styleLabel(style: string | undefined | null): string {
  return STYLES.find((s) => s.id === style)?.label ?? "";
}

/** "9:16 · 1080×1920" */
export function formatLine(format: string | undefined | null): string {
  const f = formatInfo(format);
  return `${f.id} · ${f.size}`;
}
