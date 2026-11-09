"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import type { FilmPlayback } from "@/lib/types";

import styles from "./sound.module.css";

export type EffectOption = { id: string; label: string; about: string };

type Props = {
  film: FilmPlayback;
  options: { effects: EffectOption[]; strengths: string[] } | null;
  disabled: boolean;
  onOp: (op: string, params: Record<string, unknown>) => Promise<void>;
};

/** A finishing look over the whole film or one scene: grain, vignette, glow, cinema bars, glitch. */
export function EffectsPanel({ film, options, disabled, onOp }: Props) {
  const [scene, setScene] = useState("");
  const [strength, setStrength] = useState("normal");
  const [problem, setProblem] = useState<string | null>(null);
  if (!options) return null;

  const current = (scene ? film.effects?.[scene] : undefined)?.kind ?? null;
  const filmWide = !scene && film.chapters.length > 0 && film.chapters.every((c) => film.effects?.[c.scene_id]?.kind === film.effects?.[film.chapters[0]!.scene_id]?.kind && film.effects?.[c.scene_id]);
  const active = scene ? current : filmWide ? film.effects?.[film.chapters[0]!.scene_id]?.kind ?? null : null;

  async function run(effect: string) {
    setProblem(null);
    try {
      await onOp("set_effect", { ...(scene ? { scene } : {}), effect, strength });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "That change didn't save.");
    }
  }

  return (
    <section className={styles.panel} aria-labelledby="fx-heading">
      <h2 id="fx-heading">Look</h2>
      <div className={styles.row}>
        <label htmlFor="fx-scope" className={styles.lab}>
          Effect for
        </label>
        <select id="fx-scope" className={`input ${styles.select}`} value={scene} onChange={(e) => setScene(e.target.value)} disabled={disabled}>
          <option value="">The whole film</option>
          {film.chapters.map((c, i) => (
            <option key={c.scene_id} value={c.scene_id}>
              Scene {i + 1}
            </option>
          ))}
        </select>
        <div className={styles.chips} role="radiogroup" aria-label="Strength" style={{ margin: 0 }}>
          {options.strengths.map((s) => (
            <button key={s} type="button" role="radio" aria-checked={strength === s} className={styles.chip} onClick={() => setStrength(s)} disabled={disabled}>
              {s}
            </button>
          ))}
        </div>
      </div>
      <div className={styles.chips} role="radiogroup" aria-label="Effect">
        <button type="button" role="radio" aria-checked={!active} className={styles.chip} disabled={disabled} onClick={() => run("none")}>
          None
        </button>
        {options.effects.map((e) => (
          <button key={e.id} type="button" role="radio" aria-checked={active === e.id} className={styles.chip} disabled={disabled} title={e.about} onClick={() => run(e.id)}>
            {e.label}
          </button>
        ))}
      </div>
      <p className={styles.note}>
        Grain, vignette and cinema bars take a few seconds a scene, and grain adds little to the file. Glow and glitch are drawn
        frame by frame and take longer, about as long as the scene plays or more. Only the scenes that change are cut again, and the
        film's length does not change.
      </p>
      {problem && (
        <p className={styles.problem} role="alert">
          {problem}
        </p>
      )}
    </section>
  );
}
