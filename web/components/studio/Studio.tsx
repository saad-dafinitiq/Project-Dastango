"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Dust } from "@/components/Dust";
import { TopBar, type Stage } from "@/components/TopBar";
import { api, ApiError, versioned, type SceneEdit, type ShotChange } from "@/lib/api";
import { describe, elapsedSeconds } from "@/lib/copy";
import type {
  Card,
  CastMember,
  FilmPlayback,
  FilmVersion,
  ImageBudget,
  RunResponse,
  ShotOptions,
  Storyboard,
} from "@/lib/types";
import { useProgress } from "@/lib/useProgress";

import { Dock, type RenderChoice } from "./Dock";
import { EditPanel } from "./EditPanel";
import { Player } from "./Player";
import { RenderProgress } from "./RenderProgress";
import { Sheet } from "./Sheet";
import { Versions } from "./Versions";
import sheet from "./sheet.module.css";
import styles from "./studio.module.css";

type Followed = { id: string; kind: string };

/** Jobs that change a finished film rather than make one. */
const CHANGES = new Set(["edit", "revert", "shot"]);

/** What the sheet shows while a plan is still being drawn, built from events. */
type Streamed = {
  prompt?: string;
  title: string;
  logline: string;
  cards: Card[];
  cast: CastMember[];
  cast_note?: string | null;
  images?: ImageBudget;
};

const isJob = (r: Storyboard | RunResponse): r is RunResponse => "job_id" in r;

export function Studio() {
  const router = useRouter();
  const pid = useSearchParams().get("id");

  const [board, setBoard] = useState<Storyboard | null>(null);
  const [film, setFilm] = useState<FilmPlayback | null>(null);
  const [job, setJob] = useState<Followed | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [engine, setEngine] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [showBoard, setShowBoard] = useState(false);
  const [askedFor, setAskedFor] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [versions, setVersions] = useState<FilmVersion[]>([]);
  const [restoring, setRestoring] = useState<number | null>(null);
  const [options, setOptions] = useState<ShotOptions | null>(null);
  const [lastChoice, setLastChoice] = useState<RenderChoice>({ engine: null, subtitles: "English" });
  // A finished change whose outcome no longer describes what's playing.
  const [settled, setSettled] = useState<string | null>(null);

  const { events, finished, signedOut } = useProgress(pid, job?.id ?? null);

  const refresh = useCallback(
    async (voiceEngine: string | null = engine) => {
      if (!pid) return;
      const [b, f, v] = await Promise.all([
        api.storyboard(pid, voiceEngine).catch(() => null),
        api.film(pid).catch(() => null),
        api.versions(pid).catch(() => [] as FilmVersion[]),
      ]);
      setBoard(b);
      setFilm(f);
      setVersions(v);
    },
    [pid, engine],
  );

  // First load: what is this project, and is anything running on it?
  useEffect(() => {
    if (!pid) {
      router.replace("/");
      return;
    }
    let cancelled = false;
    (async () => {
      const snap = await api.jobStatus(pid).catch(() => null);
      if (!cancelled && snap?.prompt) setAskedFor(snap.prompt);
      if (!cancelled && snap?.job_id && (snap.status === "queued" || snap.status === "running")) {
        setJob({ id: snap.job_id, kind: snap.kind ?? "plan" });
      }
      await refresh(null);
      if (!cancelled) setLoaded(true);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  useEffect(() => {
    api.shotOptions().then(setOptions).catch(() => undefined);
  }, []);

  // When the job we're following ends, read back what it settled into.
  useEffect(() => {
    if (finished) void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  useEffect(() => {
    if (signedOut) window.location.assign("/");
  }, [signedOut]);

  const last = events[events.length - 1];
  const failed = last?.phase === "error" && last.status === "failed";
  const running = job !== null && !finished;
  const changing = job !== null && CHANGES.has(job.kind);
  // A second-by-second clock while something runs; otherwise enough to keep "4 min ago" honest.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), running ? 1000 : 30_000);
    return () => clearInterval(t);
  }, [running]);

  // The plan streams the script first and then each frame as it is drawn.
  const streamed = useMemo<Streamed | null>(() => {
    const script = events.find((e) => e.phase === "storyboard" && e.status === "script");
    if (!script?.payload) return null;
    const urls = new Map<string, string>();
    for (const e of events) {
      if (e.phase === "storyboard" && e.status === "frame" && e.payload) {
        urls.set(String(e.payload.scene_id), String(e.payload.preview_url));
      }
    }
    // Portraits arrive one by one after the script, each as its own event.
    const portraits = new Map<string, { portrait_url: string | null; drawn: boolean }>();
    for (const e of events) {
      if (e.phase === "storyboard" && e.status === "portrait" && e.payload) {
        portraits.set(String(e.payload.character_id), {
          portrait_url: (e.payload.portrait_url as string | null) || null,
          drawn: Boolean(e.payload.drawn),
        });
      }
    }
    const p = script.payload as unknown as Streamed & { scenes: Card[] };
    return {
      prompt: p.prompt,
      title: p.title,
      logline: p.logline,
      cast: (p.cast ?? []).map((c) => ({ ...c, ...portraits.get(c.character_id) })),
      cast_note: p.cast_note ?? null,
      images: p.images,
      cards: p.scenes.map((c) => ({ ...c, preview_url: urls.get(c.scene_id) ?? null })),
    };
  }, [events]);

  type Mode = "loading" | "planning" | "storyboard" | "rendering" | "stopped" | "watch" | "missing";
  let mode: Mode;
  if (!loaded) mode = "loading";
  else if (running && job?.kind === "plan") mode = "planning";
  else if (running && !changing) mode = "rendering";
  else if (job?.kind === "render" && failed) mode = "stopped";
  // An edit keeps you with the film: it plays on while the change is made.
  else if (film && (!showBoard || running)) mode = "watch";
  else if (board || streamed) mode = "storyboard";
  else mode = "missing";

  const cards: Card[] = board?.frames ?? streamed?.cards ?? [];
  const title = board?.title || streamed?.title || film?.title || "";
  const prompt = board?.prompt || streamed?.prompt || askedFor || "";
  const images = board?.images ?? streamed?.images;
  const castMembers = board?.cast ?? streamed?.cast ?? [];
  const castNote = board?.cast_note ?? streamed?.cast_note ?? null;
  // A render that stopped reports how long it ran, not how long ago it started.
  const elapsed = elapsedSeconds(events, failed && last?.created_at ? Date.parse(`${last.created_at}Z`) : now);
  const seconds = cards.reduce((n, c) => n + (c.estimated_ms ?? 0), 0) / 1000 || board?.length?.target_s || 30;

  async function render(choice: RenderChoice) {
    if (!pid) return;
    setLastChoice(choice);
    setStarting(true);
    setProblem(null);
    try {
      const run = await api.render(pid, {
        tts_engine: choice.engine,
        with_subtitles: Boolean(choice.subtitles),
        subtitle_language: choice.subtitles ?? "English",
        burn_subtitles: true,
      });
      setShowBoard(false);
      setJob({ id: run.job_id, kind: "render" });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Couldn't start the render.");
    } finally {
      setStarting(false);
    }
  }

  async function saveScene(sceneId: string, edit: SceneEdit) {
    if (!pid) return;
    setProblem(null);
    try {
      const saved = await api.editScene(pid, sceneId, edit);
      await refresh();
      // The words are saved even when the picture could not be redrawn; say so.
      if (saved.redraw && !saved.redraw.ok) setProblem(saved.redraw.message ?? null);
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "That edit didn't save.");
      throw err;
    }
  }

  async function changeShot(sceneId: string, shotId: string, change: ShotChange) {
    if (!pid) return;
    setProblem(null);
    try {
      const answer = await api.editShot(pid, sceneId, shotId, change);
      if (isJob(answer)) {
        // A finished film is cut again: follow that like any change to it.
        setShowBoard(false);
        setJob({ id: answer.job_id, kind: "shot" });
      } else {
        await refresh();
      }
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "That shot change didn't save.");
    }
  }

  async function stop() {
    if (job) await api.cancel(job.id).catch(() => undefined);
  }

  async function change(query: string) {
    if (!pid) return;
    setProblem(null);
    try {
      const run = await api.edit(pid, query);
      setJob({ id: run.job_id, kind: "edit" });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Couldn't start that change.");
    }
  }

  async function goBack(version: number) {
    if (!pid) return;
    setRestoring(version);
    setProblem(null);
    if (job) setSettled(job.id);
    try {
      await api.goBack(pid, version);
      await refresh();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Couldn't go back to that version.");
    } finally {
      setRestoring(null);
    }
  }

  if (mode === "loading")
    return (
      <>
        <TopBar />
        <div className={styles.loading} aria-busy="true" />
      </>
    );

  if (mode === "missing") {
    return (
      <>
        <TopBar />
        <section className={styles.missing}>
          <h1 className="title">This film isn&rsquo;t here.</h1>
          <p>It may belong to another account, or it was never made.</p>
          <Link className="btn" href="/">
            Start a new one
          </Link>
        </section>
      </>
    );
  }

  const stage: Stage = mode === "planning" ? "write" : mode === "storyboard" ? "review" : mode === "watch" ? "watch" : "render";
  const stageFilm = title || undefined;

  return (
    <div className={styles.shell}>
      <Dust />
      <TopBar stage={stage} film={stageFilm} />

      <div className={styles.body}>
        {(mode === "rendering" || mode === "stopped") && (
          <RenderProgress
            cards={cards}
            events={events}
            now={now}
            elapsed={elapsed}
            failed={mode === "stopped"}
            failure={failed ? last?.message : undefined}
            filmSeconds={seconds}
            onStop={stop}
            onTryAgain={() => render(lastChoice)}
            onBack={() => {
              setJob(null);
              setShowBoard(true);
            }}
          />
        )}

        {(mode === "planning" || mode === "storyboard") && (
          <main className={sheet.page}>
            <header className={sheet.head}>
              <div>
                <h1 className="title">{title || prompt || "Writing the script"}</h1>
                <p className={sheet.meta}>
                  {mode === "planning"
                    ? prompt
                    : [`${cards.length} scenes`, `about ${Math.round(seconds)} s`, streamed?.logline || board?.logline].filter(Boolean).join(" · ")}
                </p>
              </div>
              {castMembers.length > 0 && (
                <ul className={sheet.cast} aria-label="Cast">
                  {castMembers.map((m) => (
                    <li key={m.character_id}>
                      {m.portrait_url && m.drawn !== false ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={m.portrait_url} alt="" />
                      ) : (
                        <span className={sheet.stand}>{(m.name.trim()[0] ?? "?").toUpperCase()}</span>
                      )}
                      {m.name}
                    </li>
                  ))}
                </ul>
              )}
            </header>
            {castNote && <p className={sheet.castNote}>{castNote}</p>}
            {mode === "planning" && (
              <p className={sheet.status} aria-live="polite">
                <span className="pip" /> {describe(last)}
                {elapsed != null && <span className="meta">· {elapsed}s</span>}
                <button className="btn quiet small" onClick={stop}>
                  Stop
                </button>
              </p>
            )}
            {failed && mode === "planning" && (
              <p className={sheet.problem} role="alert">
                {last?.message}
              </p>
            )}
            {problem && (
              <p className={sheet.problem} role="alert">
                {problem}
              </p>
            )}

            <Sheet
              cards={cards}
              cast={castMembers}
              planning={mode === "planning"}
              editable={mode === "storyboard" && Boolean(board)}
              hasFilm={Boolean(film)}
              options={options}
              onSaveScene={saveScene}
              onShot={changeShot}
            />
            {mode === "storyboard" && film && (
              <p style={{ marginTop: 22 }}>
                <button className="btn quiet" onClick={() => setShowBoard(false)}>
                  Back to the film
                </button>
              </p>
            )}
          </main>
        )}

        {mode === "watch" && film && (
          <main className={styles.page}>
            <header className={styles.head}>
              <h1 className="title">{title || prompt}</h1>
              {prompt && title && <p className={styles.sub}>{prompt}</p>}
              {problem && (
                <p className={styles.problem} role="alert">
                  {problem}
                </p>
              )}
            </header>
            {/* Keyed by version: a new cut reloads the player and its tracks. */}
            <Player key={film.version} film={film} />

            {film.stand_in_scenes.length > 0 && !running && (
              <div className={styles.standNote} role="note">
                <p>
                  <b>
                    {film.stand_in_scenes.length === 1 ? "One picture is a stand-in." : `${film.stand_in_scenes.length} pictures are stand-ins.`}
                  </b>{" "}
                  The image service was busy while drawing{" "}
                  {film.stand_in_scenes.map((id) => `scene ${film.chapters.findIndex((c) => c.scene_id === id) + 1}`).join(" and ")}.
                  Redraw {film.stand_in_scenes.length === 1 ? "it" : "them"} now that it may have caught up.
                </p>
                <button
                  className="btn small"
                  disabled={running}
                  onClick={() => {
                    const n = film.chapters.findIndex((c) => c.scene_id === film.stand_in_scenes[0]) + 1;
                    void change(`Regenerate scene ${n}`);
                  }}
                >
                  Redraw {film.stand_in_scenes.length === 1 ? "it" : "the first"}
                </button>
              </div>
            )}

            <div className={styles.watchActions}>
              <a className="btn primary" href={versioned(film.video_url, film.version)} download>
                Download
              </a>
              <button className="btn" onClick={() => setShowBoard(true)} disabled={running}>
                Edit the shots
              </button>
              <span className="meta">
                {film.width}×{film.height} · {film.fps}fps · version {film.cut}
              </span>
            </div>
            <div className={styles.afterRender}>
              <EditPanel
                film={film}
                cast={board?.cast ?? []}
                events={changing && job?.id !== settled ? events : []}
                running={running && changing}
                elapsed={changing ? elapsed : null}
                onSubmit={change}
                onStop={stop}
              />
              <Versions versions={versions} locked={running} restoring={restoring} now={now} onGoBack={goBack} />
            </div>
          </main>
        )}
      </div>

      {mode === "storyboard" && board && (
        <Dock
          scenes={cards.length}
          seconds={seconds}
          images={images}
          length={board.length}
          busy={starting}
          onEngineChange={(e) => {
            setEngine(e);
            void refresh(e);
          }}
          onRender={render}
        />
      )}
    </div>
  );
}
