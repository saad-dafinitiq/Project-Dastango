"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PlanBackdrop } from "@/components/PlanBackdrop";
import { PlanThought } from "@/components/PlanThought";
import { TopBar, type Stage } from "@/components/TopBar";
import { api, ApiError, versioned, type SceneEdit, type ShotChange } from "@/lib/api";
import { describe, elapsedSeconds, reason } from "@/lib/copy";
import { formatVars } from "@/lib/format";
import type {
  Card,
  CastMember,
  FilmPlayback,
  FilmSpec,
  FilmVersion,
  ImageBudget,
  RunResponse,
  MusicCatalogue,
  ShotOptions,
  Storyboard,
  TransitionStyle,
} from "@/lib/types";
import { useProgress } from "@/lib/useProgress";

import { Dock, type RenderChoice } from "./Dock";
import { EditPanel } from "./EditPanel";
import { ChangeBar } from "./ChangeBar";
import { Inspector } from "./Inspector";
import { Player } from "./Player";
import { RenderProgress } from "./RenderProgress";
import { Sheet } from "./Sheet";
import { SpecBar } from "./SpecBar";
import { SoundPanel } from "./SoundPanel";
import { EffectsPanel, type EffectOption } from "./EffectsPanel";
import { TitlesPanel } from "./TitlesPanel";
import { Versions } from "./Versions";
import { opItems, overlayCards, overlayFilm, stacked, type Pending } from "./staged";
import sheet from "./sheet.module.css";
import styles from "./studio.module.css";

type Followed = { id: string; kind: string };

/** Jobs that change a finished film rather than make one. */
const CHANGES = new Set(["edit", "revert", "shot", "op", "ops"]);

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
  const [musicCatalogue, setMusicCatalogue] = useState<MusicCatalogue | null>(null);
  const [effectOptions, setEffectOptions] = useState<{ effects: EffectOption[]; strengths: string[] } | null>(null);
  const [transitions, setTransitions] = useState<{ styles: TransitionStyle[]; speeds: Record<string, number> } | null>(null);
  const [lastChoice, setLastChoice] = useState<RenderChoice>({ engine: null, subtitles: "English" });
  // A finished change whose outcome no longer describes what's playing.
  const [settled, setSettled] = useState<string | null>(null);
  // Changes chosen on a finished film and waiting for Apply. Choosing never starts the (slow) re-cut.
  const [pending, setPending] = useState<Pending[]>([]);
  const [applying, setApplying] = useState(false);
  // The job Stop was pressed for: said at once, before the server's own "Cancelling" event arrives.
  const [stopping, setStopping] = useState<string | null>(null);

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
    api.musicCatalogue().then(setMusicCatalogue).catch(() => undefined);
    api
      .editOps()
      .then((c) => {
        const o = c.ops.find((x) => x.name === "set_transition")?.options;
        if (o?.styles && o.speeds) setTransitions({ styles: o.styles, speeds: o.speeds });
        const fx = c.ops.find((x) => x.name === "set_effect")?.options;
        if (fx?.effects && fx.strengths) setEffectOptions({ effects: fx.effects, strengths: fx.strengths });
      })
      .catch(() => undefined);
  }, []);

  // When the job we're following ends, read back what it settled into.
  useEffect(() => {
    if (finished) {
      const undone = events[events.length - 1]?.payload?.restyle_failed as string[] | undefined;
      if (undone?.length) {
        setProblem(
          `${undone.length === 1 ? "One picture" : `${undone.length} pictures`} could not be redrawn just now (the image services may be at their limit). ` +
            "They keep their old look on this page and are drawn fresh when you render.",
        );
      }
      if (applying) {
        // Everything stacked was applied: the stack is empty. If it stopped, it stays so it can be tried again.
        const end = events[events.length - 1];
        setApplying(false);
        if (!(end?.phase === "error" && end.status === "failed") && end?.phase !== "cancelled") setPending([]);
      }
      void refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  // Leaving with changes that were never applied would lose them without a word.
  useEffect(() => {
    if (pending.length === 0) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending.length]);

  useEffect(() => {
    if (signedOut) window.location.assign("/");
  }, [signedOut]);

  const last = events[events.length - 1];
  const failed = last?.phase === "error" && last.status === "failed";
  const running = job !== null && !finished;
  // Asked to stop, not stopped yet: a running job ends at its next check, which can be some seconds away.
  // The event log says it too (the server writes one when Stop is pressed), so a page opened after, or a
  // second tab, shows it as well.
  const cancelling = running && (stopping === job?.id || events.some((e) => e.phase === "cancelling"));
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
  else if (running && (job?.kind === "plan" || job?.kind === "respec")) mode = "planning";
  else if (running && !changing) mode = "rendering";
  else if (job?.kind === "render" && failed) mode = "stopped";
  // An edit keeps you with the film: it plays on while the change is made.
  // A shot or operation started from the storyboard is followed there, in the side panel, so the board is not left.
  else if (film && (!showBoard || (running && job?.kind !== "op" && job?.kind !== "shot"))) mode = "watch";
  else if (board || streamed) mode = "storyboard";
  else mode = "missing";

  const redrawing = job?.kind === "respec" && running;
  const cards: Card[] = overlayCards(
    (redrawing ? streamed?.cards : undefined) ?? board?.frames ?? streamed?.cards ?? [],
    pending,
    transitions?.speeds ?? {},
  );
  // The film as the panels show it: what is playing, with the stacked changes shown as made.
  const shown = film ? overlayFilm(film, pending) : film;
  const title = board?.title || streamed?.title || film?.title || "";
  const prompt = board?.prompt || streamed?.prompt || askedFor || "";
  const images = board?.images ?? streamed?.images;
  const spec = board?.spec ?? (film ? { format: film.format, style: film.style } : undefined);
  const format = spec?.format ?? "16:9";
  const castMembers = board?.cast ?? streamed?.cast ?? [];
  const castNote = board?.cast_note ?? streamed?.cast_note ?? null;
  // A render that stopped reports how long it ran, not how long ago it started.
  const elapsed = elapsedSeconds(events, failed && last?.created_at ? Date.parse(`${last.created_at}Z`) : now);
  // A plan that was written on this page leaves its thought line behind, settled ("Planned in 14.2 s"), above the storyboard.
  // The tunnel of light behind the plan is up until there is a script to show; nothing of the storyboard is drawn over it.
  const [tunnel, setTunnel] = useState(false);
  const sawPlanning = useRef(false);
  const planElapsed = useRef<number | null>(null);
  if (mode === "planning") {
    sawPlanning.current = true;
    if (elapsed != null) planElapsed.current = elapsed;
  }
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
    const params: Record<string, unknown> = { scene: sceneId, shot_id: shotId };
    if (change.reset) params.reset = true;
    if (change.move) params.move = change.move;
    if (change.framing) params.framing = change.framing;
    await runOp("set_camera", params);
  }

  async function redraw(change: { format?: string; style?: string }) {
    if (!pid) return;
    setProblem(null);
    try {
      const run = await api.respec(pid, change);
      setJob({ id: run.job_id, kind: "respec" });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Couldn't change the look.");
      throw err;
    }
  }

  /** One change from a click. On a finished film it is checked against the film and the rest of the stack and
   *  added to the stack, to be applied with the others; before a render it is cheap and is made at once. */
  async function runOp(op: string, params: Record<string, unknown>) {
    if (!pid) return;
    setProblem(null);
    try {
      if (!film) {
        await api.applyOp(pid, op, params);
        await refresh();
        return;
      }
      const next = stacked(pending, op, params);
      const { labels } = await api.checkOps(pid, opItems(next));
      // The words come from the server, which knows what each would do; sentences keep their own.
      let k = 0;
      setPending(next.map((p) => (p.kind === "op" ? { ...p, label: labels[k++] ?? p.label } : p)));
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "That change couldn't be added.");
    }
  }

  /** Apply everything stacked: the changes to the film in one job, then each typed sentence after it. */
  async function applyPending() {
    if (!pid || pending.length === 0 || running) return;
    setProblem(null);
    try {
      let lastJob: { id: string; kind: string } | null = null;
      const ops = opItems(pending);
      if (ops.length) {
        const a = await api.applyOps(pid, ops);
        if (isJob(a)) lastJob = { id: a.job_id, kind: "ops" };
      }
      for (const p of pending) {
        if (p.kind === "sentence") {
          const r = await api.edit(pid, p.query);
          lastJob = { id: r.job_id, kind: "edit" };
        }
      }
      if (lastJob) {
        setApplying(true);
        setJob(lastJob);
      } else {
        setPending([]);
        await refresh();
      }
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Couldn't apply the changes.");
    }
  }

  /** What a change in progress is doing, for the side panel of the storyboard and the film alike. */
  function changeStrip(watchLink: boolean) {
    const evs = changing && job?.id !== settled ? events : [];
    const end = evs[evs.length - 1];
    if (!end) return null;
    const bad = end.phase === "error" && end.status === "failed";
    const ok = end.phase === "complete";
    return (
      <p className={`${styles.inspStatus} ${bad ? styles.statusFailed : ""}`} role="status">
        {running && changing && <span className="pip" />}
        <span>{bad ? `Couldn’t make that change: ${reason(end.message)}` : ok ? "Change made." : cancelling && changing && running ? "Cancelling… the step that is running has to finish first." : describe(end)}</span>
        {running && changing && elapsed != null && <span className="meta">· {elapsed}s</span>}
        {running && changing && !cancelling && (
          <button className="btn quiet small" type="button" onClick={stop}>
            Stop
          </button>
        )}
        {ok && watchLink && (
          <button className="textlink" type="button" onClick={() => setShowBoard(false)}>
            Watch the film
          </button>
        )}
      </p>
    );
  }

  async function stop() {
    if (!job || cancelling) return;
    setStopping(job.id);
    try {
      await api.cancel(job.id);
    } catch (err) {
      setStopping(null);
      setProblem(err instanceof ApiError ? err.message : "Couldn't stop it. Try again in a moment.");
    }
  }

  /** A typed change is added to the stack like any other; it is understood when the stack is applied. */
  async function change(query: string) {
    const text = query.trim();
    if (!text) return;
    setProblem(null);
    setPending((p) => [...p, { id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, kind: "sentence", key: `s:${text}`, query: text, label: `“${text}”` }]);
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
    <div className={styles.shell} style={formatVars(format) as React.CSSProperties}>
      <PlanBackdrop active={mode === "planning" && running && !failed && cards.length === 0} onBusy={setTunnel} />
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
            cancelling={cancelling}
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
                {spec && mode === "storyboard" && (
                  <SpecBar
                    spec={spec as FilmSpec}
                    scenes={cards.length}
                    cast={castMembers.length}
                    canChange={!film}
                    onRedraw={redraw}
                  />
                )}
              </div>
              {castMembers.length > 0 && !tunnel && (
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
            {((mode === "planning" && !failed) || (sawPlanning.current && mode !== "planning")) && (
              <div className={sheet.status} aria-live="polite">
                <PlanThought
                  events={events}
                  working={mode === "planning" && !failed}
                  elapsed={mode === "planning" ? elapsed : planElapsed.current}
                  onStop={stop}
                />
              </div>
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
              cards={tunnel ? [] : cards}
              cast={castMembers}
              planning={mode === "planning"}
              editable={mode === "storyboard" && Boolean(board)}
              hasFilm={Boolean(film)}
              busy={running && changing}
              status={changeStrip(true)}
              options={options}
              transitions={transitions}
              onSaveScene={saveScene}
              onShot={changeShot}
              onOp={runOp}
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
          <main className={`${styles.page} ${styles.watchPage} ${pending.length > 0 || applying ? styles.barUp : ""}`}>
            <div className={styles.stageCol}>
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
            </div>

            <Inspector
              startClosed
              status={(tab) => (tab === "change" ? null : changeStrip(false))}
              tabs={[
                {
                  id: "change",
                  label: "Change",
                  content: (
                    <EditPanel
                      film={film}
                      cast={board?.cast ?? []}
                      events={changing && job?.id !== settled ? events : []}
                      running={running && changing}
                      cancelling={cancelling && changing}
                      elapsed={changing ? elapsed : null}
                      onSubmit={change}
                      onStop={stop}
                    />
                  ),
                },
                { id: "sound", label: "Sound", content: <SoundPanel film={shown ?? film} catalogue={musicCatalogue} disabled={running} onOp={runOp} /> },
                { id: "look", label: "Look", content: <EffectsPanel film={shown ?? film} options={effectOptions} disabled={running} onOp={runOp} /> },
                { id: "titles", label: "Titles", content: <TitlesPanel film={shown ?? film} disabled={running} onOp={runOp} /> },
                {
                  id: "versions",
                  label: `Versions (${versions.length})`,
                  content: <Versions versions={versions} locked={running} restoring={restoring} now={now} onGoBack={goBack} />,
                },
              ]}
            />
          </main>
        )}
      </div>

      <ChangeBar
        pending={pending}
        applying={applying}
        cancelling={cancelling}
        elapsed={changing ? elapsed : null}
        raised={mode === "storyboard"}
        onApply={applyPending}
        onDiscard={() => setPending([])}
        onRemove={(id) => setPending((p) => p.filter((x) => x.id !== id))}
        onStop={stop}
      />

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
