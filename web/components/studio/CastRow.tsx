import type { CastMember } from "@/lib/types";

import styles from "./studio.module.css";

type Props = {
  cast: CastMember[];
  /** Said when a character the creator named had no lines and is not in the cast. */
  note?: string | null;
  /** The plan is still running, so a missing portrait is on its way. */
  planning: boolean;
};

function initial(name: string): string {
  return (name.trim()[0] ?? "?").toUpperCase();
}

/** Who is in the film, above the scenes: face, name, who they are, and their voice. */
export function CastRow({ cast, note, planning }: Props) {
  if (cast.length === 0) return null;
  return (
    <section className={styles.cast} aria-label="Cast">
      <p className="label">Cast</p>
      <ul className={styles.castList}>
        {cast.map((m, i) => {
          const pending = planning && !m.portrait_url;
          const picture = Boolean(m.portrait_url) && m.drawn !== false;
          return (
            <li
              key={m.character_id}
              className={`${styles.member} rise`}
              style={{ "--delay": `${i * 70}ms` } as React.CSSProperties}
              aria-busy={pending}
            >
              <div className={`${styles.portrait} ${pending ? styles.pending : ""}`}>
                {picture ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={m.portrait_url!} alt={`Portrait of ${m.name}`} />
                ) : pending ? (
                  <span className={`label ${styles.drawing}`}>drawing</span>
                ) : (
                  <span className={styles.stand} role="img" aria-label={`No portrait yet for ${m.name}`}>
                    {initial(m.name)}
                  </span>
                )}
              </div>
              <h3 className={styles.memberName}>{m.name}</h3>
              <p className="label">{m.role}</p>
              {m.description && <p className={styles.memberAbout}>{m.description}</p>}
              {m.voice && <p className="meta">Voice · {m.voice}</p>}
              {!picture && !pending && (
                <p className={styles.memberNote}>Portrait not drawn yet; it is drawn when you render.</p>
              )}
            </li>
          );
        })}
      </ul>
      {note && <p className={styles.castNote} role="note">{note}</p>}
    </section>
  );
}
