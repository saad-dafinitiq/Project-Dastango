"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { kindOf, statusLine } from "@/lib/films";
import type { Film } from "@/lib/types";

import styles from "./films.module.css";

export type LibraryFilm = Film & { working?: string | null };

/** Every film of this account, newest first. `null` while it loads. */
export function useFilms(): LibraryFilm[] | null {
  const [films, setFilms] = useState<LibraryFilm[] | null>(null);
  useEffect(() => {
    api
      .films()
      .then((all) => setFilms([...(all as LibraryFilm[])].sort((a, b) => (b.updated_at > a.updated_at ? 1 : -1))))
      .catch(() => setFilms([]));
  }, []);
  return films;
}

type Filter = "all" | "ready" | "draft";

/** One film: its frame, its name, and one status line (a dot and a word). */
export function FilmCard({ film, delay = 0, heading: Heading = "h3" }: { film: LibraryFilm; delay?: number; heading?: "h2" | "h3" }) {
  const status = statusLine(film);
  return (
    <li className="rise" style={{ "--delay": `${delay}ms` } as React.CSSProperties}>
      <Link href={`/studio/?id=${encodeURIComponent(film.project_id)}`} className={styles.film}>
        <div className={`${styles.poster} ${film.format === "9:16" ? styles.tall : film.format === "1:1" ? styles.square : ""}`}>
          {film.poster_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={film.poster_url} alt="" loading="lazy" />
          ) : (
            <span className={styles.none}>no frame yet</span>
          )}
        </div>
        <Heading className={styles.title}>{film.title || "Untitled"}</Heading>
        <p className={styles.status}>
          <i className={`dot ${status.kind === "working" ? "now" : status.kind === "draft" ? "wait" : ""}`} />
          {status.text}
        </p>
      </Link>
    </li>
  );
}

export function FilmTabs({
  films,
  filter,
  onChange,
}: {
  films: LibraryFilm[];
  filter: Filter;
  onChange: (f: Filter) => void;
}) {
  const ready = films.filter((f) => kindOf(f) === "ready").length;
  const tabs: { id: Filter; label: string }[] = [
    { id: "all", label: `All ${films.length}` },
    { id: "ready", label: `Ready ${ready}` },
    { id: "draft", label: `Drafts ${films.length - ready}` },
  ];
  return (
    <div className={styles.tabs} role="group" aria-label="Show">
      {tabs.map((t) => (
        <button key={t.id} type="button" aria-pressed={filter === t.id} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Filtering and search, shared by the Films page. */
export function useFiltered(films: LibraryFilm[], filter: Filter, query: string) {
  return useMemo(() => {
    const q = query.trim().toLowerCase();
    return films.filter((f) => {
      if (filter === "ready" && kindOf(f) !== "ready") return false;
      if (filter === "draft" && kindOf(f) === "ready") return false;
      if (!q) return true;
      return `${f.title} ${f.prompt}`.toLowerCase().includes(q);
    });
  }, [films, filter, query]);
}

/** `heading`: the level of each film's name, one below the heading the list sits under (h2 on the Films page, whose
 *  title is the h1; h3 on the home page, under "Your films"), so no level is skipped. */
export function FilmGrid({ films, heading = "h3" }: { films: LibraryFilm[]; heading?: "h2" | "h3" }) {
  return (
    <ul className={styles.grid}>
      {films.map((f, i) => (
        <FilmCard key={f.project_id} film={f} delay={Math.min(i, 8) * 40} heading={heading} />
      ))}
    </ul>
  );
}

/** What fills the page when there is nothing to list yet. */
export function EmptyFilms() {
  return (
    <p className={styles.empty}>
      Nothing here yet. Write a sentence and your first film will appear, at the storyboard stage until you render it.
    </p>
  );
}

export type { Filter };
