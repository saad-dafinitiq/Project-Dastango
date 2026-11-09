"use client";

import Link from "next/link";
import { useState } from "react";

import { Dust } from "./Dust";
import { EmptyFilms, FilmGrid, FilmTabs, useFilms, useFiltered, type Filter } from "./FilmGrid";
import { TopBar } from "./TopBar";
import styles from "./films.module.css";

/** Every film, with its state, length and date, a filter and a search (F12). */
export function FilmsPage() {
  const films = useFilms();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const shown = useFiltered(films ?? [], filter, query);

  return (
    <div style={{ isolation: "isolate", position: "relative", minHeight: "100dvh" }}>
      <Dust />
      <TopBar here="films" />
      <main className={styles.page}>
        <h1 className="title">Your films</h1>
        <p className={styles.lede}>Everything you have made, newest first.</p>

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
              <label className="sr-only" htmlFor="film-search">
                Search your films
              </label>
              <input
                id="film-search"
                className={`input ${styles.search}`}
                type="search"
                placeholder="Search by name or idea"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            {shown.length === 0 ? <p className={styles.empty}>No film matches that.</p> : <FilmGrid films={shown} heading="h2" />}
          </>
        )}
      </main>
    </div>
  );
}
