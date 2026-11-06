"use client";

import { useEffect, useRef, useState } from "react";

import type { Pending } from "./staged";
import styles from "./changebar.module.css";

type Props = {
  pending: Pending[];
  /** Applying now: the stack is being made into a new version of the film. */
  applying: boolean;
  elapsed: number | null;
  /** On the storyboard the render bar sits along the bottom, so this one rises above it. */
  raised: boolean;
  onApply: () => void;
  onDiscard: () => void;
  onRemove: (id: string) => void;
  onStop: () => void;
};

/** The stack of changes waiting to be applied, bottom left: how many, what they are (each can be taken
 *  out), Apply, and Discard. Nothing here changes the film until Apply is pressed. */
export function ChangeBar({ pending, applying, elapsed, raised, onApply, onDiscard, onRemove, onStop }: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  if (pending.length === 0 && !applying) return null;
  const n = pending.length;
  const words = `${n} change${n === 1 ? "" : "s"}`;

  return (
    <div ref={root} className={`${styles.wrap} ${raised ? styles.raised : ""}`} role="region" aria-label="Changes waiting to be applied">
      {open && !applying && (
        <ul className={styles.list} aria-label="Waiting changes">
          {pending.map((p) => (
            <li key={p.id}>
              <span>{p.label || (p.kind === "sentence" ? p.query : "Change")}</span>
              <button type="button" className={styles.x} aria-label={`Take out: ${p.label}`} onClick={() => onRemove(p.id)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className={styles.bar}>
        {applying ? (
          <>
            <span className="pip" />
            <span className={styles.text} role="status">
              Applying {words}
              {elapsed != null && <span className="meta"> · {elapsed}s</span>}
            </span>
            <button type="button" className="btn quiet small" onClick={onStop}>
              Stop
            </button>
          </>
        ) : (
          <>
            <button type="button" className={styles.count} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
              <i className={styles.dot} aria-hidden="true" />
              {words} waiting
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={open ? styles.up : undefined}>
                <path d="m6 15 6-6 6 6" />
              </svg>
            </button>
            <button type="button" className="textlink" onClick={onDiscard}>
              Discard
            </button>
            <button type="button" className="btn primary" onClick={onApply}>
              Apply
            </button>
          </>
        )}
      </div>
    </div>
  );
}
