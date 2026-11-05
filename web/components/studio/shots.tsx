import type { CSSProperties } from "react";

import type { CardShot } from "@/lib/types";

import styles from "./sheet.module.css";

/** A shot's picture. A medium is the wide picture framed tighter, drawn by cropping it
 *  here, so it costs no image; a close-up is the speaker's portrait; a cutaway is drawn
 *  when the film is rendered, so it has no picture yet. */
export function ShotPicture({
  shot,
  preview,
  portrait,
}: {
  shot: Pick<CardShot, "framing" | "crop" | "character">;
  preview?: string | null;
  portrait?: string;
}) {
  if (shot.framing === "cutaway") return <span className={styles.later}>drawn when rendered</span>;
  if (shot.framing === "close") {
    // eslint-disable-next-line @next/next/no-img-element
    return portrait ? <img src={portrait} alt="" style={{ objectPosition: "50% 22%" }} /> : <span className={styles.later}>{shot.character}</span>;
  }
  if (!preview) return <span className={styles.later} />;
  if (shot.framing === "medium" && shot.crop) {
    // Scale the picture up by 1/w and put the kept window's centre in view.
    const { w, x, y } = shot.crop;
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={preview}
        alt=""
        style={{
          width: `${100 / w}%`,
          maxWidth: "none",
          height: "auto",
          position: "absolute",
          left: `${-x * (100 / w - 100)}%`,
          top: `${-y * (100 / w - 100)}%`,
        }}
      />
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={preview} alt="" />;
}

/** The same moves the camera makes, drawn as a small diagram: where the frame goes. */
export function MoveGlyph({ move }: { move: string }) {
  const stroke = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  let body: React.ReactNode;
  switch (move) {
    case "push_in":
      body = (
        <>
          <rect x="3" y="2" width="20" height="13" rx="1.5" {...stroke} opacity=".4" />
          <rect x="8" y="5" width="10" height="7" rx="1" {...stroke} />
        </>
      );
      break;
    case "pull_out":
      body = (
        <>
          <rect x="8" y="5" width="10" height="7" rx="1" {...stroke} opacity=".4" />
          <rect x="3" y="2" width="20" height="13" rx="1.5" {...stroke} />
        </>
      );
      break;
    case "pan_left":
      body = <path d="M20 8.5H6M10 4.5l-4 4 4 4" {...stroke} />;
      break;
    case "pan_right":
      body = <path d="M6 8.5h14M16 4.5l4 4-4 4" {...stroke} />;
      break;
    case "tilt_up":
      body = <path d="M13 14V3M9 7l4-4 4 4" {...stroke} />;
      break;
    case "tilt_down":
      body = <path d="M13 3v11M9 10l4 4 4-4" {...stroke} />;
      break;
    case "drift":
      body = <path d="M6 13 20 4M15 4h5v5" {...stroke} />;
      break;
    case "handheld":
      body = <path d="M3 8.5c2-4 3 4 5 0s3 4 5 0 3 4 5 0 2 2 3 0" {...stroke} />;
      break;
    default: // static_hold
      body = <rect x="5" y="3" width="16" height="11" rx="1.5" {...stroke} />;
  }
  return (
    <svg className={styles.glyph} viewBox="0 0 26 17" aria-hidden="true">
      {body}
    </svg>
  );
}

export const MOVE_NAMES: Record<string, string> = {
  push_in: "Push in",
  pull_out: "Pull out",
  pan_left: "Pan left",
  pan_right: "Pan right",
  tilt_up: "Tilt up",
  tilt_down: "Tilt down",
  drift: "Drift",
  static_hold: "Hold still",
  handheld: "Handheld",
};

export const FRAMING_NAMES: Record<string, string> = {
  wide: "Wide",
  medium: "Medium",
  close: "Close-up",
  cutaway: "Cutaway",
};

/** "0:04" from milliseconds. */
export function mmss(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function moveStyle(move: string): CSSProperties {
  return { "--move": move } as CSSProperties;
}
