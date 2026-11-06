"use client";

import { useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { FilmPlayback, MusicCatalogue } from "@/lib/types";

import styles from "./sound.module.css";

type Props = {
  film: FilmPlayback;
  catalogue: MusicCatalogue | null;
  disabled: boolean;
  onOp: (op: string, params: Record<string, unknown>) => Promise<void>;
};

const LEVELS: { id: "music" | "voices" | "ambience"; label: string; max: number }[] = [
  { id: "music", label: "Music", max: 200 },
  { id: "voices", label: "Voices", max: 200 },
  { id: "ambience", label: "Ambience", max: 200 },
];

/** The sound of the film: which music plays (all of it, or one scene), the ambience under it, and
 *  how loud each is. Every choice is one shared editing operation, applied at once. */
export function SoundPanel({ film, catalogue, disabled, onOp }: Props) {
  const sound = film.sound;
  const [scene, setScene] = useState<string>("");
  const [listing, setListing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [rights, setRights] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [levels, setLevels] = useState<Record<string, number>>({});
  const file = useRef<HTMLInputElement>(null);
  const player = useRef<HTMLAudioElement>(null);

  if (!sound || !catalogue) return null;
  const scope = scene ? { scene } : {};
  const whereWords = scene ? `scene ${film.chapters.findIndex((c) => c.scene_id === scene) + 1}` : "the whole film";
  // For the whole film, a mood is current only when every scene has it.
  const moodList = Object.values(sound.moods);
  const currentMood = scene ? sound.moods[scene] : moodList.length && moodList.every((m) => m === moodList[0]) ? moodList[0] : null;
  const playing = scene ? sound.scene_music[scene] : sound.music_track;

  async function run(op: string, params: Record<string, unknown>) {
    setProblem(null);
    try {
      await onOp(op, params);
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "That change didn't save.");
    }
  }

  async function upload(f: File) {
    setUploading(true);
    setProblem(null);
    try {
      const form = new FormData();
      form.append("file", f);
      form.append("rights", String(rights));
      const res = await fetch(`/api/edit/${film.project_id}/music`, { method: "POST", body: form, credentials: "same-origin" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : "That file couldn't be added.");
      await run("set_music", { ...scope, track: body.track });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "That file couldn't be added.");
    } finally {
      setUploading(false);
      if (file.current) file.current.value = "";
    }
  }

  function listen(id: string) {
    if (!player.current) return;
    player.current.src = `/api/edit/music/${id}/file`;
    void player.current.play();
  }

  const tracks = catalogue.tracks.filter((t) => !t.upload);
  const mine = sound.uploads;

  return (
    <section className={styles.panel} aria-labelledby="sound-heading">
      <h2 id="sound-heading">Sound</h2>

      <div className={styles.row}>
        <label htmlFor="sound-scope" className={styles.lab}>
          Music for
        </label>
        <select id="sound-scope" className={`input ${styles.select}`} value={scene} onChange={(e) => setScene(e.target.value)} disabled={disabled}>
          <option value="">The whole film</option>
          {film.chapters.map((c, i) => (
            <option key={c.scene_id} value={c.scene_id}>
              Scene {i + 1}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn small"
          disabled={disabled}
          onClick={() => run("set_music", { ...scope, off: true })}
          aria-label={`No music in ${whereWords}`}
        >
          No music
        </button>
        <button type="button" className="textlink" disabled={disabled} onClick={() => run("set_music", { ...scope, reset: true })}>
          As it was made
        </button>
      </div>

      <div className={styles.chips} role="radiogroup" aria-label={`Mood of the music in ${whereWords}`}>
        {catalogue.moods.map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={!playing && currentMood === m && sound.music_on}
            className={styles.chip}
            disabled={disabled}
            onClick={() => run("set_music", { ...scope, mood: m })}
          >
            {m}
          </button>
        ))}
      </div>

      <button type="button" className="textlink" aria-expanded={listing} onClick={() => setListing((v) => !v)}>
        {listing ? "Hide the tracks" : `Choose a track (${tracks.length})`}
      </button>
      {listing && (
        <ul className={styles.tracks}>
          {[...tracks, ...mine.map((u) => ({ id: u.id, title: u.title, mood: "yours", seconds: u.seconds, licence: "yours", upload: true }))].map((t) => (
            <li key={t.id} className={playing === t.id ? styles.on : undefined}>
              {!t.upload && (
                <button type="button" className={styles.listen} onClick={() => listen(t.id)} aria-label={`Listen to ${t.title}`}>
                  ▶
                </button>
              )}
              <span className={styles.name}>
                {t.title} <small>{t.mood}</small>
              </span>
              <button type="button" className="textlink" disabled={disabled} onClick={() => run("set_music", { ...scope, track: t.id })}>
                Use for {whereWords}
              </button>
            </li>
          ))}
        </ul>
      )}
      <audio ref={player} preload="none" />

      <div className={styles.upload}>
        <label className={styles.rights}>
          <input type="checkbox" checked={rights} onChange={(e) => setRights(e.target.checked)} /> I have the right to use this music
        </label>
        <input ref={file} type="file" hidden accept={catalogue.limits.extensions.join(",")} onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
        <button type="button" className="btn small" disabled={disabled || uploading || !rights} onClick={() => file.current?.click()}>
          {uploading ? "Adding…" : "Add your own"}
        </button>
        <span className="meta">
          up to {catalogue.limits.max_mb} MB, {catalogue.limits.min_seconds} s to {catalogue.limits.max_seconds / 60} min
        </span>
      </div>

      <h3 className={styles.sub}>Ambience</h3>
      <div className={styles.chips} role="radiogroup" aria-label={`Ambience in ${whereWords}`}>
        {[{ id: "none", label: "None" }, ...catalogue.ambience].map((a) => {
          const on = (sound.ambience[scene || "*"] ?? (scene ? sound.ambience["*"] : undefined) ?? "none") === a.id;
          return (
            <button
              key={a.id}
              type="button"
              role="radio"
              aria-checked={on}
              className={styles.chip}
              disabled={disabled}
              onClick={() => run("set_ambience", { ...scope, kind: a.id })}
            >
              {a.label}
            </button>
          );
        })}
      </div>

      <h3 className={styles.sub}>Levels</h3>
      {LEVELS.map((l) => {
        const server = l.id === "music" ? sound.music_pct : l.id === "voices" ? sound.voices_pct : sound.ambience_pct;
        const value = levels[l.id] ?? server;
        return (
          <div className={styles.level} key={l.id}>
            <label htmlFor={`lv-${l.id}`}>{l.label}</label>
            <input
              id={`lv-${l.id}`}
              type="range"
              min={0}
              max={l.max}
              step={5}
              value={value}
              disabled={disabled}
              onChange={(e) => setLevels({ ...levels, [l.id]: Number(e.target.value) })}
              onPointerUp={() => value !== server && run("set_volume", { target: l.id, percent: value })}
              onKeyUp={() => value !== server && run("set_volume", { target: l.id, percent: value })}
              aria-valuetext={`${value} percent`}
            />
            <output htmlFor={`lv-${l.id}`}>{value}%</output>
          </div>
        );
      })}
      <p className={styles.note}>100% is how the film was made. The whole film is kept at one loudness, so raising one level lowers the others.</p>
      {problem && (
        <p className={styles.problem} role="alert">
          {problem}
        </p>
      )}
    </section>
  );
}
