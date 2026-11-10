"use client";

import { ago } from "@/lib/copy";
import type { FilmVersion } from "@/lib/types";

import { Reveal } from "./Reveal";
import styles from "./studio.module.css";

type Props = {
  versions: FilmVersion[];
  /** Nothing can be restored while a job is changing the film. */
  locked: boolean;
  restoring: number | null;
  now: number;
  onGoBack: (version: number) => void;
};

/** Every cut of this film, newest first. Going back is itself a new version,
 * so nothing is ever lost by trying it. */
export function Versions({ versions, locked, restoring, now, onGoBack }: Props) {
  if (versions.length === 0) return null;
  const newestFirst = [...versions].reverse();

  return (
    <section className={styles.versions} aria-labelledby="versions-heading">
      <h2 id="versions-heading">
        Versions
      </h2>
      <ol className={styles.versionList}>
        {newestFirst.map((v) => (
          <Reveal key={v.version} distance={18}>
          <li className={`${styles.version} ${v.current ? styles.versionOn : ""}`}>
            <span className={`meta ${styles.versionNum}`}>v{v.number}</span>
            <span className={styles.versionBody}>
              <span className={styles.versionLabel}>
                {v.kind === "edit" ? `“${v.label}”` : v.label}
              </span>
              <span className={styles.versionMeta}>
                {v.kind === "edit" && v.detail ? `${v.detail} · ` : ""}
                {ago(v.created_at, now)}
              </span>
            </span>
            {v.current ? (
              <span className={styles.playing}><i className="dot" /> Playing</span>
            ) : (
              <button
                className="btn quiet small"
                type="button"
                disabled={locked || restoring !== null}
                onClick={() => onGoBack(v.version)}
              >
                {restoring === v.version ? "Going back…" : "Go back"}
              </button>
            )}
          </li>
          </Reveal>
        ))}
      </ol>
    </section>
  );
}
