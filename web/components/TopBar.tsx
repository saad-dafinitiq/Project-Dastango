"use client";

import Link from "next/link";

import { useSession } from "./AuthGate";
import { Logo } from "./Logo";
import styles from "./topbar.module.css";
import { useTheme } from "./useTheme";

/** The four moments of making a film, in the order they happen. */
export type Stage = "write" | "review" | "render" | "watch";
const STAGES: { id: Stage; label: string }[] = [
  { id: "write", label: "Write" },
  { id: "review", label: "Review" },
  { id: "render", label: "Render" },
  { id: "watch", label: "Watch" },
];

type Props = {
  /** Where in the film's making this screen is; omitted outside a film. */
  stage?: Stage;
  /** The film's name, shown after the logo. */
  film?: string;
  /** The page this is, for the nav's current mark. */
  here?: "films" | "docs";
};

export function TopBar({ stage, film, here }: Props) {
  const { user, github, notice, dismiss, signOut } = useSession();
  const { choice, choose } = useTheme();
  const at = stage ? STAGES.findIndex((s) => s.id === stage) : -1;
  const initial = (user.email.trim()[0] ?? "?").toUpperCase();

  return (
    <>
      <header className={styles.bar}>
        <div className={styles.left}>
          <Link href="/" className={styles.mark} aria-label="Dastango, make a film">
            <Logo />
          </Link>
          {film && (
            <>
              <span className={styles.slash} aria-hidden="true">
                /
              </span>
              <span className={styles.film}>{film}</span>
            </>
          )}
        </div>

        {stage ? (
          <nav className={styles.steps} aria-label="Stages">
            {STAGES.map((s, i) => {
              const state = i < at ? "done" : i === at ? "now" : "";
              return (
                <span key={s.id} className={state} aria-current={i === at ? "step" : undefined}>
                  <i className={`${styles.stp} ${state ? styles[state] : ""}`} />
                  {s.label}
                </span>
              );
            })}
          </nav>
        ) : (
          <span />
        )}

        <nav className={styles.nav} aria-label="Account and help">
          <Link href="/films/" aria-current={here === "films" ? "page" : undefined}>
            Films
          </Link>
          <Link className={styles.help} href="/docs/" aria-current={here === "docs" ? "page" : undefined}>
            How it works
          </Link>
          <details className={styles.account}>
            <summary aria-label="Your account">{initial}</summary>
            <div className={styles.menu}>
              <p className={styles.who}>{user.email}</p>
              <p className={styles.themeLabel} id="theme-label">
                Appearance
              </p>
              <div className={styles.themes} role="group" aria-labelledby="theme-label">
                {(["system", "light", "dark"] as const).map((t) => (
                  <button key={t} type="button" aria-pressed={choice === t} onClick={() => choose(t)}>
                    {t === "system" ? "Auto" : t === "light" ? "Light" : "Dark"}
                  </button>
                ))}
              </div>
              {github.enabled && !github.connected && (
                // Linking needs a signed-in account, so it lives here, not on the sign-in page.
                <a href="/api/auth/github/start?mode=connect">Connect GitHub</a>
              )}
              <Link href="/docs/">How it works</Link>
              <button type="button" onClick={signOut}>
                Sign out
              </button>
            </div>
          </details>
        </nav>
      </header>
      {notice && (
        <p
          className={`${styles.notice} ${notice.kind === "error" ? styles.noticeError : ""}`}
          role={notice.kind === "error" ? "alert" : "status"}
        >
          {notice.text}
          <button className="btn quiet small" onClick={dismiss} aria-label="Dismiss">
            ×
          </button>
        </p>
      )}
    </>
  );
}
