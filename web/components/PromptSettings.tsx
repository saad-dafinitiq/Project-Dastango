"use client";

import { useEffect, useRef, useState } from "react";

import { formatInfo, styleLabel, type FormatId, type StyleId } from "@/lib/format";

import { ChoiceGroup, FormatChoice, StyleChoice } from "./Choice";
import styles from "./promptsettings.module.css";

const LENGTHS = [20, 30, 45, 60];
const SCENES = [2, 3, 4, 5, 6];
const LEAVE_MS = 160;

type Props = {
  format: FormatId;
  style: StyleId;
  seconds: number;
  scenes: number;
  onFormat: (v: FormatId) => void;
  onStyle: (v: StyleId) => void;
  onSeconds: (v: number) => void;
  onScenes: (v: number) => void;
  disabled?: boolean;
};

/** The settings of a film behind one "+": the box stays one line of words, what was chosen is said
 *  beside the button, and the choices open in a small modal window. A native <dialog> does the
 *  hard parts (top layer, so nothing on the page can cover it; focus kept inside; Escape; focus
 *  returned to the button), which a hand-placed drop-down kept getting wrong. */
export function PromptSettings({ format, style, seconds, scenes, onFormat, onStyle, onSeconds, onScenes, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [explain, setExplain] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (open && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [open]);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  function close() {
    if (leaving) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setLeaving(true);
    timer.current = setTimeout(
      () => {
        dialog.current?.close();
        setOpen(false);
        setLeaving(false);
        setExplain(false);
      },
      still ? 0 : LEAVE_MS,
    );
  }

  const summary = `${format} · ${styleLabel(style)} · ${seconds} s · ${scenes} scenes`;

  return (
    <div className={styles.root}>
      <button
        type="button"
        className={styles.plus}
        aria-label="Film settings"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>
      <button type="button" className={styles.summary} disabled={disabled} onClick={() => setOpen(true)} aria-label={`Settings: ${summary}. Change`}>
        {summary}
      </button>

      {open && (
        <dialog
          ref={dialog}
          className={`${styles.modal} ${leaving ? styles.leaving : ""}`}
          aria-labelledby="film-settings-title"
          onCancel={(e) => {
            e.preventDefault();
            close();
          }}
          onClick={(e) => {
            // A click on the scrim, outside the window itself.
            if (e.target === dialog.current) close();
          }}
        >
          <div className={styles.window}>
            <header className={styles.title}>
              <h2 id="film-settings-title">Film settings</h2>
              <button type="button" className={styles.x} aria-label="Close" onClick={close}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </header>

            <div className={styles.groups}>
              <FormatChoice value={format} onChange={onFormat} />
              <StyleChoice value={style} onChange={onStyle} />
              <div className={styles.pair}>
                <ChoiceGroup label="Length" value={seconds} onChange={onSeconds} compact options={LENGTHS.map((s) => ({ id: s, title: `${s} s`, sub: "" }))} />
                <ChoiceGroup label="Scenes" value={scenes} onChange={onScenes} compact options={SCENES.map((n) => ({ id: n, title: String(n), sub: "" }))} />
              </div>
            </div>

            <div className={styles.foot}>
              <span>
                {formatInfo(format).size} · {scenes} pictures to plan, nothing more until you say so
              </span>
              <button type="button" className={styles.help} aria-expanded={explain} onClick={() => setExplain((v) => !v)}>
                What is a scene?
              </button>
            </div>
            {explain && (
              <div className={styles.explain} role="note">
                <p>
                  <b>A scene</b> is one place and moment in the story, with its own picture and its own lines. Each scene is cut into two to four shots.
                </p>
                <p>
                  {seconds} seconds in {scenes} scenes is about {Math.round(seconds / scenes)} seconds a scene. More scenes make a busier film; fewer make longer, calmer scenes.
                </p>
              </div>
            )}
            <div className={styles.actions}>
              <button type="button" className="btn primary" onClick={close}>
                Done
              </button>
            </div>
          </div>
        </dialog>
      )}
    </div>
  );
}
