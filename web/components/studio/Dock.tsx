"use client";

import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { ImageBudget, LengthCheck, VoiceCatalogue } from "@/lib/types";

import styles from "./sheet.module.css";

export type RenderChoice = { engine: string | null; subtitles: string | null };

type Props = {
  scenes: number;
  /** About how long the film will be, in seconds, from the plan. */
  seconds: number;
  images: ImageBudget | undefined;
  length?: LengthCheck | null;
  busy: boolean;
  onEngineChange: (engine: string | null) => void;
  onRender: (choice: RenderChoice) => void;
};

/** The bottom of the sheet: what rendering will spend, its choices, and the one
 *  button that spends it. */
export function Dock({ scenes, seconds, images, length, busy, onEngineChange, onRender }: Props) {
  const [catalogue, setCatalogue] = useState<VoiceCatalogue | null>(null);
  const [languages, setLanguages] = useState<string[]>([]);
  const [engine, setEngine] = useState<string>("");
  const [subtitles, setSubtitles] = useState<string>("English");
  const [sampleNote, setSampleNote] = useState<string | null>(null);
  const [hearing, setHearing] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    api.voices().then(setCatalogue).catch(() => undefined);
    api.languages().then(setLanguages).catch(() => setLanguages(["English"]));
  }, []);

  const current = catalogue?.engines.find((e) => e.name === (engine || catalogue.default));

  async function hear() {
    if (!current) return;
    setHearing(true);
    setSampleNote(null);
    try {
      const sample = await api.previewVoice(current.name, current.voices[0]?.id ?? "");
      if (sample.fell_back) setSampleNote(`${current.label} couldn't speak. That was ${sample.engine}.`);
      if (!audio.current) audio.current = new Audio();
      audio.current.src = sample.url;
      await audio.current.play().catch(() => undefined);
    } catch (err) {
      setSampleNote(err instanceof ApiError ? err.message : "No sample this time.");
    } finally {
      setHearing(false);
    }
  }

  const off = length && !length.within;

  return (
    <footer className={styles.dock}>
      <p className={styles.tot}>
        <b>{scenes} scenes</b> · about <b>{Math.round(seconds)} s</b>
        {images && images.render > 0 ? (
          <>
            {" "}
            · <b>{images.render}</b> more images to draw
          </>
        ) : null}
      </p>
      <div className={styles.cta}>
        {off ? (
          <p>
            This script plans about {Math.round(length.planned_s)} s for the {length.target_s} s you asked for. You can
            lengthen or shorten lines first.
          </p>
        ) : (
          <p>You can leave once it starts. The film waits in Films.</p>
        )}
        <details className={styles.optsBox}>
          <summary>Voices and subtitles</summary>
          <div className={styles.optsPanel}>
            <label className="field">
              <span className="label">Voices</span>
              <div className={styles.inline}>
                <select
                  className="select"
                  value={engine}
                  onChange={(e) => {
                    setEngine(e.target.value);
                    onEngineChange(e.target.value || null);
                  }}
                >
                  <option value="">{catalogue ? (catalogue.engines.find((e) => e.is_default)?.label ?? "Default") : "Default"}</option>
                  {catalogue?.engines
                    .filter((e) => !e.is_default)
                    .map((e) => (
                      <option key={e.name} value={e.name} disabled={!e.available}>
                        {e.label}
                        {e.available ? "" : " (unavailable)"}
                      </option>
                    ))}
                </select>
                <button type="button" className="btn small" onClick={hear} disabled={!current || hearing}>
                  {hearing ? "Listening" : "Hear it"}
                </button>
              </div>
            </label>
            {sampleNote && <p className="meta">{sampleNote}</p>}
            <label className="field">
              <span className="label">Subtitles</span>
              <select className="select" value={subtitles} onChange={(e) => setSubtitles(e.target.value)}>
                <option value="">None</option>
                {languages.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </details>
        <button className="btn ember" disabled={busy} onClick={() => onRender({ engine: engine || null, subtitles: subtitles || null })}>
          {busy ? "Starting" : "Render this film"}
        </button>
      </div>
    </footer>
  );
}
