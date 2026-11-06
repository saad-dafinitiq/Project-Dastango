"use client";

import { useState } from "react";

import { FormatChoice, StyleChoice } from "@/components/Choice";
import { formatLine, styleLabel, type FormatId, type StyleId } from "@/lib/format";
import type { FilmSpec } from "@/lib/types";

import styles from "./sheet.module.css";

type Props = {
  spec: FilmSpec;
  scenes: number;
  cast: number;
  /** Only before the render: a finished film's format and style are fixed. */
  canChange: boolean;
  onRedraw: (change: { format?: FormatId; style?: StyleId }) => Promise<void>;
};

/** What the film was made as, under its title, and the one place to change it (DST-34).
 *  Changing it draws every picture again, so the cost is said before anything happens. */
export function SpecBar({ spec, scenes, cast, canChange, onRedraw }: Props) {
  const [open, setOpen] = useState(false);
  const [format, setFormat] = useState<FormatId>(spec.format);
  const [style, setStyle] = useState<StyleId>((spec.style || "realistic") as StyleId);
  const [busy, setBusy] = useState(false);
  const pictures = scenes + cast;
  const changed = format !== spec.format || style !== (spec.style || "realistic");
  const check = spec.scene_check;

  async function redraw() {
    setBusy(true);
    try {
      await onRedraw({
        ...(format !== spec.format ? { format } : {}),
        ...(style !== (spec.style || "realistic") ? { style } : {}),
      });
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className={styles.spec}>
        <b>{formatLine(spec.format)}</b>
        {spec.style && (
          <>
            <i aria-hidden="true" />
            <b>{styleLabel(spec.style)}</b>
          </>
        )}
        {canChange && (
          <button type="button" className="textlink" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            Change style or format
          </button>
        )}
      </div>
      {check && !check.within && (
        <p className={styles.castNote} role="note">
          You asked for {check.asked} scenes; the script has {check.got}.{" "}
          {check.got < check.asked ? "A story cannot be stretched without inventing one, so it was left as written." : ""}
        </p>
      )}
      {open && (
        <section className={styles.chg} aria-label="Change style or format">
          <div>
            <h3>Change the look of this film</h3>
            <p className={styles.sub}>Choose what to change. Nothing happens until you press Redraw.</p>
            <div className={styles.chgChoices}>
              <FormatChoice value={format} onChange={setFormat} disabled={busy} />
              <StyleChoice value={style} onChange={setStyle} disabled={busy} />
            </div>
          </div>
          <div>
            <p className={styles.cost}>
              <b>This redraws all {pictures} pictures</b> ({scenes} scenes and {cast} characters) and uses image quota.
            </p>
            <p className={styles.keep}>
              Your words, your shot choices and the length stay as they are. After you render, the format and style cannot
              be changed: make a new film instead.
            </p>
            <div className={styles.acts}>
              <button className="btn primary" disabled={!changed || busy} onClick={redraw}>
                {busy ? "Starting" : `Redraw ${pictures} pictures`}
              </button>
              <button className="textlink" onClick={() => setOpen(false)} disabled={busy}>
                Cancel
              </button>
            </div>
          </div>
        </section>
      )}
    </>
  );
}
