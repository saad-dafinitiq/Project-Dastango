"use client";

import Link from "next/link";
import { useState } from "react";

import StarBorder from "@/vendor/react-bits/StarBorder";

import { api, ApiError } from "@/lib/api";

import { Dust } from "./Dust";
import { EmptyFilms, FilmTabs, useFilms, useFiltered, type Filter, type LibraryFilm } from "./FilmGrid";
import { FilmFolder, FOLDERS } from "./FilmFolder";
import { TopBar } from "./TopBar";
import { useIsDark } from "./useTheme";
import styles from "./films.module.css";

/** Every film, in a folder for each shape of film, with a filter, a search (F12) and a pin that puts a film on Create. */
export function FilmsPage() {
  const films = useFilms();
  const dark = useIsDark();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  // What the creator has just pinned or unpinned, until the list is fetched again: the page answers at once.
  const [changed, setChanged] = useState<Record<string, boolean>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const shown = useFiltered(films ?? [], filter, query);

  const isPinned = (f: LibraryFilm) => changed[f.project_id] ?? Boolean(f.pinned);
  const pinnedCount = (films ?? []).filter(isPinned).length;

  async function pin(film: LibraryFilm, on: boolean) {
    setProblem(null);
    setChanged((c) => ({ ...c, [film.project_id]: on }));
    try {
      await api.pinFilm(film.project_id, on);
    } catch (err) {
      setChanged((c) => ({ ...c, [film.project_id]: !on }));
      setProblem(err instanceof ApiError ? err.message : "Couldn't change the pin. Try again in a moment.");
    }
  }

  return (
    <div style={{ isolation: "isolate", position: "relative", minHeight: "100dvh" }}>
      <Dust />
      <TopBar here="films" />
      <main className={styles.page}>
        <h1 className="title">Your films</h1>
        <p className={styles.lede}>
          Everything you have made, newest first, in a folder for each shape. Pin a film to show it on Create
          {pinnedCount > 0 ? ` (${pinnedCount} pinned)` : ""}.
        </p>

        {films === null ? (
          <div aria-busy="true" style={{ minHeight: "40vh" }} />
        ) : films.length === 0 ? (
          <>
            <EmptyFilms />
            <p style={{ marginTop: 20 }}>
              <Link className="btn primary" href="/">
                Write the first one
              </Link>
            </p>
          </>
        ) : (
          <>
            <div className={styles.bar}>
              <FilmTabs films={films} filter={filter} onChange={setFilter} />
              <StarBorder
                as="div"
                className={styles.searchWrap}
                radius={12}
                thickness={1}
                duration={5}
                glow={0.5}
                theme={dark ? "dark" : "light"}
                color={dark ? "#ffffff" : "#923d17"}
                backgroundColor="rgba(var(--s1-rgb), 0.9)"
                borderColor="var(--line2)"
                textColor="var(--fg)"
              >
                <label className="sr-only" htmlFor="film-search">
                  Search your films
                </label>
                <input
                  id="film-search"
                  className={styles.searchInput}
                  type="search"
                  placeholder="Search by name or idea"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </StarBorder>
            </div>
            {problem && (
              <p className={styles.problem} role="alert">
                {problem}
              </p>
            )}
            {shown.length === 0 ? (
              <p className={styles.empty}>No film matches that.</p>
            ) : (
              FOLDERS.map((folder) => {
                const here = shown.filter((f) => (f.format ?? "16:9") === folder.ratio);
                return here.length > 0 ? <FilmFolder key={folder.ratio} folder={folder} films={here} pinned={isPinned} onPin={pin} /> : null;
              })
            )}
          </>
        )}
      </main>
    </div>
  );
}
