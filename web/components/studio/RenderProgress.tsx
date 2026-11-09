"use client";

import { clock, renderView, typicalTime, type StageState } from "@/lib/renderProgress";
import { reason } from "@/lib/copy";
import type { Card, ProgressEvent } from "@/lib/types";

import styles from "./render.module.css";

type Props = {
  cards: Card[];
  events: ProgressEvent[];
  now: number;
  elapsed: number | null;
  failed: boolean;
  /** Stop was pressed: it ends at the next safe point, which may be some seconds away. */
  cancelling?: boolean;
  /** The failure's reason, from the last event. */
  failure?: string;
  /** The film's planned length, for the "usually takes" line. */
  filmSeconds: number;
  onStop: () => void;
  onTryAgain: () => void;
  onBack: () => void;
};

const dotClass = (s: StageState) => (s === "now" ? "dot now" : s === "bad" ? "dot bad" : s === "wait" ? "dot wait" : "dot");

/** What a render says while it runs: what it is doing now (from real step counts), what
 *  is done and how long each took, how long a film this long usually takes, and that
 *  leaving is safe (F3). When it stops, what is kept and how to go on (F13). */
export function RenderProgress({ cards, events, now, elapsed, failed, cancelling = false, failure, filmSeconds, onStop, onTryAgain, onBack }: Props) {
  const view = renderView(events, now, failed);
  const usual = typicalTime(filmSeconds);

  return (
    <>
      <div className={styles.work}>
        <section className={styles.main} aria-live="polite">
          <h1 className="title">{failed ? "The render stopped." : cancelling ? "Cancelling the render…" : view.headline}</h1>
          <p className={`${styles.lede} ${failed ? styles.problem : ""}`}>
            {failed
              ? `${(reason(failure) || "Something went wrong").replace(/[.\s]+$/, "")}. Your storyboard and your edits are kept. Trying again renders from the start.`
              : cancelling
                ? "Stopping at the next safe point: the step that is running has to finish first, so this can take a moment. Your storyboard and your edits are kept."
                : `A film this long usually takes ${usual}. You can leave: it will be in Films when it's done.`}
          </p>
          {!failed && (
            <div className={styles.bar} role="progressbar" aria-label="Render progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(view.fraction * 100)}>
              <b style={{ width: `${Math.max(2, view.fraction * 100)}%` }} />
            </div>
          )}

          <ul className={styles.grid} aria-label="The scenes">
            {cards.map((c, i) => (
              <li key={c.scene_id}>
                <div className={`${styles.pic} ${!failed ? styles.scan : ""}`}>
                  {c.preview_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={c.preview_url} alt="" style={failed ? undefined : { filter: "brightness(.8)" }} />
                  )}
                </div>
                <div className={styles.cap}>
                  <h3>
                    {i + 1} {c.title}
                  </h3>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <aside className={styles.side} aria-label="Steps">
          <ol className={styles.ledger}>
            <li data-state="done">
              <span className={styles.gl}>
                <i className="dot" />
              </span>
              <span>
                <b>Script written</b>
              </span>
              <span />
            </li>
            <li data-state="done">
              <span className={styles.gl}>
                <i className="dot" />
              </span>
              <span>
                <b>Storyboard approved</b>
                <small>by you</small>
              </span>
              <span />
            </li>
            {view.stages.map((s) => (
              <li key={s.id} data-state={s.state}>
                <span className={styles.gl}>
                  <i className={dotClass(s.state)} />
                </span>
                <span>
                  <b>{failed && s.state === "bad" ? `Stopped: ${s.label.toLowerCase()}` : s.label}</b>
                  {s.detail && s.state !== "wait" && <small>{s.detail}</small>}
                </span>
                <span className={styles.tm}>{s.seconds != null && s.state !== "wait" ? clock(s.seconds) : ""}</span>
              </li>
            ))}
          </ol>
          {!failed && (
            <p className={styles.note}>
              <b>Safe to leave.</b> The render runs on the server, not in this tab. Come back to this film from Films at any time.
            </p>
          )}
        </aside>
      </div>

      <footer className={styles.dock}>
        <div className={styles.strip} aria-label="The film, scene by scene">
          {cards.map((c) => (
            <i key={c.scene_id}>
              {c.preview_url && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={c.preview_url} alt="" />
              )}
            </i>
          ))}
        </div>
        <div className={styles.cta}>
          <p>
            <span className={styles.clock}>{elapsed != null ? clock(elapsed) : "00:00"}</span> {failed ? "before it stopped" : "so far"}
            <br />
            {failed ? "Nothing in your storyboard changed" : cancelling ? "Cancelling…" : "You can leave"}
          </p>
          {failed ? (
            <>
              <button className="btn" onClick={onBack}>
                Back to the storyboard
              </button>
              <button className="btn ember" onClick={onTryAgain}>
                Try again
              </button>
            </>
          ) : (
            <button className="btn" onClick={onStop} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Stop rendering"}
            </button>
          )}
        </div>
      </footer>
    </>
  );
}
