"use client";

import { useState } from "react";

import type { Card, CardShot, TransitionStyle } from "@/lib/types";

import styles from "./sheet.module.css";
import { ShotPicture } from "./shots";

const Check = () => (
  <svg className={styles.ck} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="m3 8.5 3.2 3.2L13 5" />
  </svg>
);

/** A small diagram of each style: two frames and what happens between them. */
export function TransitionGlyph({ kind }: { kind: string }) {
  const s = { fill: "none", stroke: "currentColor", strokeWidth: 1.4, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  let body: React.ReactNode;
  switch (kind) {
    case "cut":
      body = (
        <>
          <rect x="2" y="3" width="8" height="11" rx="1.2" {...s} />
          <rect x="16" y="3" width="8" height="11" rx="1.2" {...s} />
          <path d="M13 2v13" {...s} strokeWidth={1.6} />
        </>
      );
      break;
    case "fadeblack":
      body = (
        <>
          <rect x="2" y="3" width="9" height="11" rx="1.2" {...s} />
          <rect x="12" y="3" width="3" height="11" fill="currentColor" />
          <rect x="16" y="3" width="8" height="11" rx="1.2" {...s} />
        </>
      );
      break;
    case "wipe":
      body = (
        <>
          <rect x="2" y="3" width="22" height="11" rx="1.2" {...s} />
          <path d="M15 3v11M12 8.5H6M8 6l-2 2.5L8 11" {...s} />
        </>
      );
      break;
    case "slide":
      body = (
        <>
          <rect x="2" y="3" width="13" height="11" rx="1.2" {...s} opacity={0.45} />
          <rect x="11" y="3" width="13" height="11" rx="1.2" {...s} />
          <path d="M22 8.5h-5" {...s} />
        </>
      );
      break;
    case "zoom":
      body = (
        <>
          <rect x="2" y="1.5" width="22" height="14" rx="1.4" {...s} opacity={0.45} />
          <rect x="8" y="5" width="10" height="7" rx="1" {...s} />
        </>
      );
      break;
    default: // fade and the rest: one frame dissolving into another
      body = (
        <>
          <rect x="2" y="3" width="14" height="11" rx="1.2" {...s} opacity={0.45} />
          <rect x="10" y="3" width="14" height="11" rx="1.2" {...s} />
        </>
      );
  }
  return (
    <svg className={styles.glyph} viewBox="0 0 26 17" aria-hidden="true">
      {body}
    </svg>
  );
}

const SPEED_NAMES: [string, string][] = [
  ["quick", "Quick"],
  ["normal", "Normal"],
  ["slow", "Slow"],
];

/** "Soft fade", "slow wipe", "0.6 s dip to black": a transition in the words the picker uses. */
export function transitionWords(kind: string, ms: number, styles_: TransitionStyle[], speeds: Record<string, number>): string {
  const label = (styles_.find((s) => s.id === kind)?.label ?? kind).toLowerCase();
  if (kind === "cut") return label;
  const named = Object.entries(speeds).find(([, v]) => v === ms)?.[0];
  const how = named === "normal" ? "" : named ?? `${ms / 1000} s`;
  return [how, label].filter(Boolean).join(" ");
}

type Props = {
  card: Card;
  shot: CardShot;
  index: number;
  /** The shot this one comes in from, for the preview: the one before it, or the previous scene's last. */
  before?: { card: Card; shot: CardShot } | null;
  styles: TransitionStyle[];
  speeds: Record<string, number>;
  hasFilm: boolean;
  portraits: Record<string, string>;
  onChange: (change: { transition?: string; speed?: string; ms?: number; reset?: boolean }) => Promise<void>;
  /** Only where the editor stands on its own; in the side panel changes apply at once and nothing needs closing. */
  onClose?: () => void;
};

/** How one cut comes in: a style, a speed and a look at the two shots. Chosen and applied at
 *  once, to this cut only. */
export function TransitionEditor({ card, shot, index, before, styles: all, speeds, hasFilm, portraits, onChange, onClose }: Props) {
  const [busy, setBusy] = useState(false);
  const t = shot.transition;
  const [more, setMore] = useState(() => Boolean(t && !all.find((s) => s.id === t.kind)?.main));
  if (!t) return null;
  const main = all.filter((s) => s.main);
  const rest = all.filter((s) => !s.main);
  const currentSpeed = Object.entries(speeds).find(([, v]) => v === t.ms)?.[0];
  // Any length from 0.1 s to 1.5 s, not only the three named speeds (the server accepts `ms`).
  const [custom, setCustom] = useState<number | null>(null);
  const shown = custom ?? t.ms;

  async function apply(change: { transition?: string; speed?: string; ms?: number; reset?: boolean }) {
    setBusy(true);
    try {
      await onChange(change);
    } finally {
      setBusy(false);
    }
  }

  const where = t.between ? "between scenes" : `shot ${index + 1}`;

  return (
    <div className={styles.edc} role="group" aria-label={`How ${where} comes in`}>
      <div>
        <p className={styles.sub}>
          This cut only. {hasFilm ? "The film is cut again around it, which takes a little while; no new pictures are drawn and the film's length does not change." : "Nothing is drawn."}
        </p>
        <div className={styles.tgrid} role="radiogroup" aria-label="Transition">
          {main.map((s) => (
            <button
              key={s.id}
              type="button"
              role="radio"
              aria-checked={t.kind === s.id}
              className={styles.topt}
              title={s.about}
              disabled={busy}
              onClick={() => t.kind !== s.id && apply({ transition: s.id, ...(s.id !== "cut" && currentSpeed ? { speed: currentSpeed } : {}) })}
            >
              <TransitionGlyph kind={s.id} />
              <span>{s.label}</span>
              <Check />
            </button>
          ))}
        </div>
        {rest.length > 0 && (
          <div className={styles.grp}>
            <button type="button" className="textlink" aria-expanded={more} onClick={() => setMore((v) => !v)}>
              {more ? "Fewer styles" : `${rest.length} more styles`}
            </button>
            {more && (
              <div className={styles.fr} role="radiogroup" aria-label="More transitions" style={{ marginTop: 10 }}>
                {rest.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    role="radio"
                    aria-checked={t.kind === s.id}
                    className={styles.chip}
                    disabled={busy}
                    title={s.about}
                    onClick={() => t.kind !== s.id && apply({ transition: s.id, ...(currentSpeed ? { speed: currentSpeed } : {}) })}
                  >
                    <Check />
                    {s.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        <div className={styles.grp} style={{ marginTop: 0 }}>
          <b id={`tp-speed-${shot.shot_id}`}>Speed</b>
          <div className={styles.fr} role="radiogroup" aria-labelledby={`tp-speed-${shot.shot_id}`}>
            {SPEED_NAMES.map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={t.kind !== "cut" && currentSpeed === id}
                className={styles.chip}
                disabled={busy || t.kind === "cut"}
                onClick={() => apply({ transition: t.kind, speed: id })}
              >
                <Check />
                {label}
                <small style={{ color: "var(--fa)", marginLeft: 4 }}>{(speeds[id] ?? 0) / 1000} s</small>
              </button>
            ))}
          </div>
          {t.kind !== "cut" && (
            <div className={styles.fr} style={{ marginTop: 12, alignItems: "center" }}>
              <label htmlFor={`tp-ms-${shot.shot_id}`} style={{ color: "var(--mu)", fontSize: 14 }}>
                Exact length
              </label>
              <input
                id={`tp-ms-${shot.shot_id}`}
                type="range"
                min={100}
                max={1500}
                step={50}
                value={shown}
                disabled={busy}
                style={{ flex: 1, minWidth: 140, accentColor: "var(--ac)", minHeight: 40 }}
                aria-valuetext={`${(shown / 1000).toFixed(2)} seconds`}
                onChange={(e) => setCustom(Number(e.target.value))}
                onPointerUp={() => custom !== null && custom !== t.ms && void apply({ transition: t.kind, ms: custom }).then(() => setCustom(null))}
                onKeyUp={() => custom !== null && custom !== t.ms && void apply({ transition: t.kind, ms: custom }).then(() => setCustom(null))}
              />
              <output htmlFor={`tp-ms-${shot.shot_id}`} style={{ minWidth: 52, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                {(shown / 1000).toFixed(2)} s
              </output>
            </div>
          )}
          {t.kind !== "cut" && t.max_ms !== undefined && shown > t.max_ms && (
            <p className={styles.note} role="status">
              Held to {(t.max_ms / 1000).toFixed(2)} s here: a transition can take at most half of the shorter shot it joins, so a
              longer one would swallow the shot. Make the shots longer, or choose a shorter length.
            </p>
          )}
          {t.kind === "cut" && <p className={styles.note}>A cut has no speed: it is straight to the next shot.</p>}
        </div>

        {before && (
          <>
            <div className={styles.cutPreview} role="group" aria-label="The two shots this cut joins">
              <div className={styles.cutHalf}>
                <ShotPicture shot={before.shot} preview={before.card.preview_url} portrait={before.shot.character_id ? portraits[before.shot.character_id] : undefined} />
              </div>
              <div className={styles.cutHalf}>
                <ShotPicture shot={shot} preview={card.preview_url} portrait={shot.character_id ? portraits[shot.character_id] : undefined} />
              </div>
              <i />
            </div>
            <div className={styles.cutLabels} aria-hidden="true">
              <span>{before.shot.framing === "close" && before.shot.character ? before.shot.character : before.shot.framing}</span>
              <span>{shot.framing === "close" && shot.character ? shot.character : shot.framing}</span>
            </div>
          </>
        )}

        <div className={styles.acts}>
          {onClose && (
            <button className="btn primary small" onClick={onClose}>
              Done
            </button>
          )}
          {t.chosen && (
            <button className="textlink" disabled={busy} onClick={() => apply({ reset: true })}>
              Back to the default
            </button>
          )}
          {busy && (
            <span className="meta" role="status">
              Saving…
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
