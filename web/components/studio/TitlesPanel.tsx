"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import type { FilmPlayback } from "@/lib/types";

import { Reveal } from "./Reveal";
import styles from "./sound.module.css";

type Props = {
  film: FilmPlayback;
  disabled: boolean;
  onOp: (op: string, params: Record<string, unknown>) => Promise<void>;
};

const ITEMS = [
  { id: "title_card", label: "Title card", about: "The film's title over the opening seconds." },
  { id: "scene_titles", label: "Scene titles", about: "Each scene's name, top left, as it begins." },
  { id: "lower_thirds", label: "Name bars", about: "A character's name the first time they speak." },
  { id: "end_card", label: "End card", about: "“The End” over the closing seconds." },
] as const;

/** Titles over the film. They sit on the picture, so the film's length and sound do not change. */
export function TitlesPanel({ film, disabled, onOp }: Props) {
  const titles = film.titles;
  const [title, setTitle] = useState<string | null>(null);
  const [ending, setEnding] = useState<string | null>(null);
  const [credits, setCredits] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  if (!titles) return null;

  async function run(params: Record<string, unknown>) {
    setProblem(null);
    try {
      await onOp("set_graphics", params);
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "That change didn't save.");
    }
  }

  const textValue = (mine: string | null, saved: string | null) => mine ?? saved ?? "";

  return (
    <section className={styles.panel} aria-labelledby="titles-heading">
      <h2 id="titles-heading">Titles</h2>
      <ul className={styles.tracks} style={{ maxHeight: "none" }}>
        {ITEMS.map((it, n) => {
          const on = Boolean(titles[it.id]);
          return (
            <Reveal key={it.id} delay={n * 0.05}>
            <li className={on ? styles.on : undefined} title={it.about}>
              <span className={styles.name}>{it.label}</span>
              <button
                type="button"
                role="switch"
                aria-checked={on}
                aria-label={it.label}
                className={styles.chip}
                disabled={disabled}
                onClick={() => run({ [it.id]: !on })}
              >
                {on ? "On" : "Off"}
              </button>
            </li>
            </Reveal>
          );
        })}
      </ul>

      <Reveal>
      {titles.title_card && (
        <div className={styles.row}>
          <label htmlFor="t-title" className={styles.lab}>
            Title card says
          </label>
          <input
            id="t-title"
            className="input"
            maxLength={90}
            placeholder="The film's own title"
            value={textValue(title, titles.title_text)}
            onChange={(e) => setTitle(e.target.value)}
            disabled={disabled}
          />
          <button className="btn small" disabled={disabled || title === null} onClick={() => run({ title_text: title ?? "" }).then(() => setTitle(null))}>
            Save
          </button>
        </div>
      )}
      {titles.end_card && (
        <>
          <div className={styles.row}>
            <label htmlFor="t-end" className={styles.lab}>
              End card says
            </label>
            <input
              id="t-end"
              className="input"
              maxLength={90}
              placeholder="The End"
              value={textValue(ending, titles.end_text)}
              onChange={(e) => setEnding(e.target.value)}
              disabled={disabled}
            />
            <button className="btn small" disabled={disabled || ending === null} onClick={() => run({ end_text: ending ?? "" }).then(() => setEnding(null))}>
              Save
            </button>
          </div>
          <div className={styles.row}>
            <label htmlFor="t-credits" className={styles.lab}>
              Line under it
            </label>
            <input
              id="t-credits"
              className="input"
              maxLength={90}
              placeholder="e.g. Made with Dastango"
              value={textValue(credits, titles.credits_text)}
              onChange={(e) => setCredits(e.target.value)}
              disabled={disabled}
            />
            <button className="btn small" disabled={disabled || credits === null} onClick={() => run({ credits_text: credits ?? "" }).then(() => setCredits(null))}>
              Save
            </button>
          </div>
        </>
      )}
      </Reveal>
      {problem && (
        <p className={styles.problem} role="alert">
          {problem}
        </p>
      )}
    </section>
  );
}
