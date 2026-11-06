"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";

import type { FormatId, StyleId } from "@/lib/format";

import { FilmGrid, FilmTabs, useFilms, useFiltered, type Filter } from "./FilmGrid";
import { PromptSettings } from "./PromptSettings";
import { TopBar } from "./TopBar";
import styles from "./create.module.css";

// Starting points, written the way a person would actually ask for a film.
const IDEAS = [
  "The last lighthouse keeper realises the sea has started writing back",
  "Two rival street-food cooks discover they share a grandmother's recipe",
  "A night train that only stops at stations that no longer exist",
];

/** Pictures that move slowly behind the box, from the creator's own films once
 *  there are three, otherwise the shipped examples. */
const EXAMPLES = [1, 2, 3].map((n) => `/examples/frame-${n}.jpg`);

function greeting(): string {
  const h = new Date().getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

const Up2 = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 19V5M6 11l6-6 6 6" />
  </svg>
);
const Up = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M7 17 17 7M8 7h9v9" />
  </svg>
);

export function Composer() {
  const router = useRouter();
  const films = useFilms();
  const [prompt, setPrompt] = useState("");
  const [seconds, setSeconds] = useState(30);
  const [scenes, setScenes] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const [hello, setHello] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [format, setFormat] = useState<FormatId>("16:9");
  const [style, setStyle] = useState<StyleId>("realistic");

  useEffect(() => setHello(greeting()), []);

  // The last format and style chosen on this browser are the next defaults.
  useEffect(() => {
    try {
      const f = localStorage.getItem("dastango-format");
      const s = localStorage.getItem("dastango-style");
      if (f === "16:9" || f === "9:16" || f === "1:1") setFormat(f);
      if (s === "realistic" || s === "anime") setStyle(s);
    } catch {
      /* private window: the defaults stand */
    }
  }, []);

  const own = useMemo(() => (films ?? []).map((f) => f.poster_url).filter((u): u is string => Boolean(u)), [films]);
  const pictures = own.length >= 3 ? own.slice(0, 3) : EXAMPLES;
  const shown = useFiltered(films ?? [], filter, "").slice(0, 5);

  const ready = prompt.trim().length >= 4 && !busy;

  // The box is one line until the words need more, then grows to five.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
  }, [prompt]);

  async function start() {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.plan({
        prompt: prompt.trim(),
        target_duration_s: seconds,
        scene_count: scenes,
        format,
        style,
      });
      try {
        localStorage.setItem("dastango-format", format);
        localStorage.setItem("dastango-style", style);
      } catch {
        /* not kept */
      }
      router.push(`/studio/?id=${encodeURIComponent(run.project_id)}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't start. Is the server running?");
      setBusy(false);
    }
  }

  return (
    <div className={styles.app}>
      <div className={styles.stage} aria-hidden="true">
        {pictures.map((src) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={src} src={src} alt="" />
        ))}
      </div>
      <div className={styles.top}>
        <TopBar />
      </div>

      <main>
        <section className={styles.hero}>
          <div className={styles.hello}>{hello}</div>
          <h1>What happens in your film?</h1>

          <div className={styles.write}>
            <label htmlFor="prompt" className="sr-only">
              Describe your film
            </label>
            <textarea
              id="prompt"
              ref={box}
              rows={1}
              value={prompt}
              placeholder="A clockmaker in a flooded city repairs the hours people lose"
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                // Enter sends, as in every chat box; Shift+Enter is a new line.
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void start();
                }
              }}
            />
            <div className={styles.bar}>
              <PromptSettings
                format={format}
                style={style}
                seconds={seconds}
                scenes={scenes}
                onFormat={setFormat}
                onStyle={setStyle}
                onSeconds={setSeconds}
                onScenes={setScenes}
                disabled={busy}
              />
              <button className={`btn primary ${styles.send}`} disabled={!ready} onClick={start} aria-label={busy ? "Starting" : "Make the storyboard"} title="Make the storyboard (Enter)">
                {busy ? <span className="pip" /> : <Up2 />}
              </button>
            </div>
          </div>
          {error && (
            <p className={`${styles.note} ${styles.error}`} role="alert">
              {error}
            </p>
          )}

          {!prompt && (
            <div className={styles.ideas}>
              {IDEAS.map((idea) => (
                <button key={idea} type="button" className={styles.idea} onClick={() => setPrompt(idea)}>
                  {idea}
                  <Up />
                </button>
              ))}
            </div>
          )}
        </section>

        {films && films.length > 0 && (
          <section className={styles.reel} aria-labelledby="your-films">
            <div className={styles.rh}>
              <h2 id="your-films">Your films</h2>
              <FilmTabs films={films} filter={filter} onChange={setFilter} />
            </div>
            <FilmGrid films={shown} />
            {films.length > 5 && (
              <p style={{ marginTop: 24 }}>
                <Link className={styles.more} href="/films/">
                  See all {films.length} films
                </Link>
              </p>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
