"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "@/lib/api";

import { FilmGrid, FilmTabs, useFilms, useFiltered, type Filter } from "./FilmGrid";
import { TopBar } from "./TopBar";
import styles from "./create.module.css";

// Starting points, written the way a person would actually ask for a film.
const IDEAS = [
  "The last lighthouse keeper realises the sea has started writing back",
  "Two rival street-food cooks discover they share a grandmother's recipe",
  "A night train that only stops at stations that no longer exist",
];

const LENGTHS = [20, 30, 45, 60];
const SCENES = [2, 3, 4, 5, 6];

/** Pictures that move slowly behind the box, from the creator's own films once
 *  there are three, otherwise the shipped examples. */
const EXAMPLES = [1, 2, 3].map((n) => `/examples/frame-${n}.jpg`);

function greeting(): string {
  const h = new Date().getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

const Arrow = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M5 12h14M13 6l6 6-6 6" />
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
  const [explain, setExplain] = useState(false);
  const [hello, setHello] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => setHello(greeting()), []);

  const own = useMemo(() => (films ?? []).map((f) => f.poster_url).filter((u): u is string => Boolean(u)), [films]);
  const pictures = own.length >= 3 ? own.slice(0, 3) : EXAMPLES;
  const shown = useFiltered(films ?? [], filter, "").slice(0, 5);

  const ready = prompt.trim().length >= 4 && !busy;

  async function start() {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.plan({
        prompt: prompt.trim(),
        target_duration_s: seconds,
        scene_count: scenes,
      });
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
              rows={2}
              value={prompt}
              placeholder="A clockmaker in a flooded city repairs the hours people lose"
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void start();
              }}
            />
            <div className={styles.row}>
              <div className={styles.ctl}>
                <label className="sr-only" htmlFor="length">
                  Length
                </label>
                <select id="length" className="select" value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
                  {LENGTHS.map((s) => (
                    <option key={s} value={s}>
                      {s} seconds
                    </option>
                  ))}
                </select>
                <label className="sr-only" htmlFor="scenes">
                  Scenes
                </label>
                <select id="scenes" className="select" value={scenes} onChange={(e) => setScenes(Number(e.target.value))}>
                  {SCENES.map((n) => (
                    <option key={n} value={n}>
                      {n} scenes
                    </option>
                  ))}
                </select>
                <button type="button" className={styles.help} aria-expanded={explain} onClick={() => setExplain((v) => !v)}>
                  What is a scene?
                </button>
              </div>
              <div className={styles.go}>
                <span className={styles.eta}>usually under a minute</span>
                <button className="btn primary" disabled={!ready} onClick={start}>
                  {busy ? "Starting" : "Make the storyboard"}
                  {!busy && <Arrow />}
                </button>
              </div>
            </div>
            {explain && (
              <div className={styles.explain} role="note">
                <p>
                  <b>A scene</b> is one place and moment in the story, with its own picture and its own lines. Each
                  scene is cut into two to four shots.
                </p>
                <p>
                  <b>Length and scenes trade off.</b> {seconds} seconds in {scenes} scenes is about{" "}
                  {Math.round(seconds / scenes)} seconds a scene. More scenes means a busier film; fewer means longer,
                  calmer scenes. 30 seconds in 3 scenes is a good start.
                </p>
              </div>
            )}
          </div>

          {error ? (
            <p className={`${styles.note} ${styles.error}`} role="alert">
              {error}
            </p>
          ) : (
            <p className={styles.note}>
              You&rsquo;ll see every scene, its lines and a first frame before anything is rendered. {scenes} images to
              plan, and nothing more until you say so.
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
