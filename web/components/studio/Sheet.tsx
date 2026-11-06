"use client";

import { useState } from "react";

import type { SceneEdit, ShotChange } from "@/lib/api";
import type { Card, CardShot, CastMember, ShotOptions, TransitionStyle } from "@/lib/types";

import { Inspector } from "./Inspector";
import styles from "./sheet.module.css";
import { FRAMING_NAMES, MOVE_NAMES, MoveGlyph, ShotPicture, mmss } from "./shots";
import { TransitionEditor, TransitionGlyph, transitionWords } from "./TransitionEditor";

const Check = () => (
  <svg className={styles.ck} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="m3 8.5 3.2 3.2L13 5" />
  </svg>
);

type Props = {
  cards: Card[];
  cast: CastMember[];
  /** True while the plan is still being drawn: frames not yet there are on their way. */
  planning: boolean;
  /** Only once the storyboard is settled. */
  editable: boolean;
  /** A finished film exists, so a shot change cuts it again. */
  hasFilm: boolean;
  options: ShotOptions | null;
  /** The transition styles and speeds the server offers (GET /api/edit/ops). */
  transitions: { styles: TransitionStyle[]; speeds: Record<string, number> } | null;
  onSaveScene: (sceneId: string, edit: SceneEdit) => Promise<void>;
  onShot: (sceneId: string, shotId: string, change: ShotChange) => Promise<void>;
  /** One shared editing operation (DST-39), as a click sends it. */
  onOp: (op: string, params: Record<string, unknown>) => Promise<void>;
  /** A change is being made: the panel waits for it, and says what it is doing. */
  busy?: boolean;
  status?: React.ReactNode;
};

type Sel = { sceneId: string; shotId: string | null };
type ShotTab = "camera" | "transition";
type SceneTab = "words" | "scene";

/** The storyboard: scenes as rows of shot cards, and one panel beside them for whatever is chosen.
 *  Everything that can be done to a shot or a scene is in that panel, in the same place, so nothing
 *  has to be found among the cards and nothing opens out of sight (DST-40). */
export function Sheet({ cards, cast, planning, editable, hasFilm, options, transitions, onSaveScene, onShot, onOp, busy, status }: Props) {
  const [picked, setPicked] = useState<Sel | null>(null);
  const [shotTab, setShotTab] = useState<ShotTab>("camera");
  const [sceneTab, setSceneTab] = useState<SceneTab>("words");
  const [sheetOpen, setSheetOpen] = useState(false);
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const portraits = Object.fromEntries(cast.filter((m) => m.portrait_url).map((m) => [m.character_id, m.portrait_url as string]));

  const fold = (id: string, on: boolean) =>
    setFolded((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  // What is chosen. Until something is, the first shot is, so the panel is never empty.
  const first = cards[0];
  const stillThere =
    picked && cards.find((c) => c.scene_id === picked.sceneId && (picked.shotId === null || (c.shots ?? []).some((s) => s.shot_id === picked.shotId)));
  const sel: Sel | null = stillThere ? picked : first ? { sceneId: first.scene_id, shotId: first.shots?.[0]?.shot_id ?? null } : null;
  const selCard = sel ? cards.find((c) => c.scene_id === sel.sceneId) : undefined;
  const selIndex = selCard ? cards.indexOf(selCard) : -1;
  const selShot = selCard && sel?.shotId ? (selCard.shots ?? []).find((s) => s.shot_id === sel.shotId) : undefined;

  function chooseShot(sceneId: string, shotId: string, tab?: ShotTab) {
    setPicked({ sceneId, shotId });
    if (tab) setShotTab(tab);
    setSheetOpen(true);
  }
  function chooseScene(sceneId: string, tab?: SceneTab) {
    setPicked({ sceneId, shotId: null });
    if (tab) setSceneTab(tab);
    setSheetOpen(true);
  }

  const withPanel = editable && cards.length > 0;

  return (
    <div className={`${styles.board} ${withPanel ? styles.withPanel : ""}`}>
      <div className={styles.list}>
        {cards.map((card, i) => {
          const number = i + 1;
          const sceneOn = Boolean(sel && sel.sceneId === card.scene_id && sel.shotId === null);

          if (folded.has(card.scene_id) && !(sel?.sceneId === card.scene_id)) {
            return (
              <section key={card.scene_id} className={`${styles.scene} ${styles.folded}`} aria-label={`Scene ${number}, checked`}>
                <div className={styles.rail}>
                  <h2>{number}</h2>
                  <div>
                    <h3>{card.title}</h3>
                    <p>{card.shots?.length ?? 0} shots</p>
                  </div>
                </div>
                <div className={styles.mini}>
                  {card.preview_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={card.preview_url} alt="" />
                  )}
                  <span className={styles.sum}>
                    <i className="dot" /> Checked
                  </span>
                  <button className={`textlink ${styles.reopen} ${styles.mini}`} style={{ marginLeft: "auto" }} onClick={() => fold(card.scene_id, false)}>
                    Reopen
                  </button>
                </div>
              </section>
            );
          }

          const shots = card.shots ?? [];
          const linesById = new Map(card.lines.map((l) => [l.line_id, l]));

          return (
            <section key={card.scene_id} className={styles.scene} aria-label={`Scene ${number}: ${card.title}`}>
              <header className={styles.sceneHead}>
                <h2>{String(number).padStart(2, "0")}</h2>
                <div className={styles.sceneTitle}>
                  <h3>{card.title}</h3>
                  <p>{[card.tone, card.setting].filter(Boolean).join(" · ")}</p>
                  {card.staged && <p className={styles.waiting}>{card.staged}</p>}
                </div>
                {editable && (
                  <button type="button" className={styles.sceneBtn} aria-pressed={sceneOn} onClick={() => chooseScene(card.scene_id)}>
                    {sceneOn ? (
                      <>
                        <Check /> Editing scene
                      </>
                    ) : (
                      "Edit scene"
                    )}
                  </button>
                )}
              </header>

              <ol className={styles.shots} aria-label={`${shots.length} shots in scene ${number}`}>
                {shots.length === 0 && (
                  <li className={styles.card}>
                    <div className={`${styles.frame} ${card.preview_url ? "" : styles.pending}`}>
                      {card.preview_url && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={card.preview_url} alt={card.setting || card.title} />
                      )}
                    </div>
                  </li>
                )}
                {shots.map((shot, j) => {
                  const on = Boolean(sel && sel.sceneId === card.scene_id && sel.shotId === shot.shot_id);
                  const t = shot.transition;
                  const said = shot.line_ids.map((id) => linesById.get(id)).filter(Boolean);
                  const name = shot.framing === "close" && shot.character ? shot.character : FRAMING_NAMES[shot.framing] ?? shot.framing;
                  const move = MOVE_NAMES[shot.move_id] ?? shot.move;
                  const tword = t && transitions ? transitionWords(t.kind, t.ms, transitions.styles, transitions.speeds) : "";
                  const picture = (
                    <div className={`${styles.frame} ${card.preview_url || shot.framing === "cutaway" ? "" : styles.pending}`}>
                      <ShotPicture shot={shot} preview={card.preview_url} portrait={shot.character_id ? portraits[shot.character_id] : undefined} />
                      <span className={styles.tc}>{mmss(shot.start_ms)}</span>
                    </div>
                  );
                  return (
                    <li key={shot.shot_id} className={`${styles.card} rise`} style={{ "--delay": `${(i * 3 + j) * 50}ms` } as React.CSSProperties} aria-current={on ? "true" : undefined}>
                      {editable ? (
                        <button type="button" className={styles.pick} aria-label={`Edit shot ${j + 1} of scene ${number}, ${name}`} onClick={() => chooseShot(card.scene_id, shot.shot_id)}>
                          {picture}
                        </button>
                      ) : (
                        picture
                      )}
                      <div className={styles.cap}>
                        <b>{name}</b>
                        {on && (
                          <span className={styles.editing}>
                            <Check /> Editing
                          </span>
                        )}
                        {shot.pending && !on && <span className={styles.waiting}>Waiting</span>}
                      </div>
                      <div className={styles.tags}>
                        <Tag editable={editable} label={`Camera: ${move}`} onClick={() => chooseShot(card.scene_id, shot.shot_id, "camera")}>
                          <MoveGlyph move={shot.move_id} />
                          {move}
                        </Tag>
                        {t && transitions && (
                          <Tag editable={editable} label={`${t.between ? "Between scenes" : "Comes in with"}: ${tword}`} onClick={() => chooseShot(card.scene_id, shot.shot_id, "transition")}>
                            <TransitionGlyph kind={t.kind} />
                            {tword}
                          </Tag>
                        )}
                      </div>
                      {said.length > 0 ? (
                        said.map((l) => (
                          <p key={l!.line_id} className={styles.say} title={l!.text}>
                            <b>{l!.character}</b>
                            {l!.text}
                          </p>
                        ))
                      ) : (
                        <p className={`${styles.say} ${styles.none}`}>No line in this shot.</p>
                      )}
                    </li>
                  );
                })}
              </ol>
            </section>
          );
        })}
        {planning && cards.length === 0 && (
          <p className={styles.status} aria-live="polite">
            <span className="pip" /> Writing the script
          </p>
        )}
      </div>

      {withPanel && selCard && (
        <Inspector
          bottomGap={110}
          busy={busy}
          status={() => status}
          sheet={{ open: sheetOpen, onClose: () => setSheetOpen(false) }}
          title={selShot ? `Scene ${selIndex + 1} · Shot ${(selCard.shots ?? []).indexOf(selShot) + 1}` : `Scene ${selIndex + 1}`}
          subtitle={
            selShot
              ? `${selShot.framing === "close" && selShot.character ? selShot.character : FRAMING_NAMES[selShot.framing] ?? selShot.framing} · ${mmss(selShot.start_ms)}`
              : selCard.title
          }
          value={selShot ? shotTab : sceneTab}
          onValueChange={(id) => (selShot ? setShotTab(id as ShotTab) : setSceneTab(id as SceneTab))}
          tabs={
            selShot
              ? [
                  {
                    id: "camera",
                    label: "Camera",
                    content: (
                      <ShotEditor
                        key={selShot.shot_id}
                        card={selCard}
                        shot={selShot}
                        options={options}
                        hasFilm={hasFilm}
                        portraits={portraits}
                        onShot={(change) => onShot(selCard.scene_id, selShot.shot_id, change)}
                        onRedraw={() => onOp("regenerate_shot", { scene: selCard.scene_id, shot: (selCard.shots ?? []).indexOf(selShot) + 1 })}
                      />
                    ),
                  },
                  {
                    id: "transition",
                    label: "Transition",
                    content: transitions ? (
                      selShot.transition ? (
                        <TransitionEditor
                          key={selShot.shot_id}
                          card={selCard}
                          shot={selShot}
                          index={(selCard.shots ?? []).indexOf(selShot)}
                          before={previousShot(cards, selIndex, selShot)}
                          styles={transitions.styles}
                          speeds={transitions.speeds}
                          hasFilm={hasFilm}
                          portraits={portraits}
                          onChange={(change) =>
                            onOp("set_transition", {
                              scene: selCard.scene_id,
                              ...((selCard.shots ?? []).indexOf(selShot) > 0 ? { shot: (selCard.shots ?? []).indexOf(selShot) + 1 } : {}),
                              ...change,
                            })
                          }
                        />
                      ) : (
                        <p className={styles.note}>Nothing comes before the first shot of the film, so it has no transition.</p>
                      )
                    ) : (
                      <p className={styles.note}>Loading the transitions…</p>
                    ),
                  },
                ]
              : [
                  {
                    id: "words",
                    label: "Words and picture",
                    content: <SceneWords key={selCard.scene_id} card={selCard} onSave={(edit) => onSaveScene(selCard.scene_id, edit)} />,
                  },
                  {
                    id: "scene",
                    label: "Order",
                    content: (
                      <SceneActions
                        key={selCard.scene_id}
                        number={selIndex + 1}
                        count={cards.length}
                        onOp={onOp}
                        sceneId={selCard.scene_id}
                        onChecked={() => {
                          fold(selCard.scene_id, true);
                          setPicked(null);
                        }}
                      />
                    ),
                  },
                ]
          }
        />
      )}
    </div>
  );
}

function previousShot(cards: Card[], at: number, shot: CardShot): { card: Card; shot: CardShot } | null {
  const card = cards[at]!;
  const shots = card.shots ?? [];
  const j = shots.indexOf(shot);
  if (j > 0) return { card, shot: shots[j - 1]! };
  const before = at > 0 ? cards[at - 1] : undefined;
  return before?.shots?.length ? { card: before, shot: before.shots[before.shots.length - 1]! } : null;
}

/** A small label on a shot card that is also the way into its panel: the camera move opens Camera,
 *  the transition opens Transition. Plain text where nothing can be edited yet. */
function Tag({ editable, label, onClick, children }: { editable: boolean; label: string; onClick: () => void; children: React.ReactNode }) {
  if (!editable) return <span className={styles.tag}>{children}</span>;
  return (
    <button type="button" className={styles.tag} aria-label={`${label}. Change`} onClick={onClick}>
      {children}
    </button>
  );
}

/** One shot's camera move and framing, applied as soon as they are chosen. */
function ShotEditor({
  card,
  shot,
  options,
  hasFilm,
  portraits,
  onShot,
  onRedraw,
}: {
  card: Card;
  shot: CardShot;
  options: ShotOptions | null;
  hasFilm: boolean;
  portraits: Record<string, string>;
  onShot: (change: ShotChange) => Promise<void>;
  onRedraw: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const framingOk = (f: string) => f !== "close" || shot.can_close;

  async function apply(change: ShotChange) {
    setBusy(true);
    try {
      await onShot(change);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.edc} role="group" aria-label="Camera for this shot">
      <div className={styles.preview} aria-label="Preview of the move">
        <div className={styles.moving} data-move={shot.move_id}>
          <ShotPicture shot={shot} preview={card.preview_url} portrait={shot.character_id ? portraits[shot.character_id] : undefined} />
        </div>
      </div>
      <p className={styles.sub} style={{ margin: 0 }}>
        Changes apply to this shot only.{" "}
        {hasFilm ? "The film is cut again around it, which takes a little while but draws no new pictures." : "Nothing is drawn."}
      </p>

      <div className={styles.grp} style={{ marginTop: 0 }}>
        <b id={`mv-${shot.shot_id}`}>Camera move</b>
        <div className={styles.opts} role="radiogroup" aria-labelledby={`mv-${shot.shot_id}`}>
          {(options?.moves ?? []).map((m) => (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={shot.move_id === m.id}
              className={styles.opt}
              disabled={busy || shot.framing === "cutaway"}
              onClick={() => shot.move_id !== m.id && apply({ move: m.id })}
            >
              <MoveGlyph move={m.id} />
              <span>
                {MOVE_NAMES[m.id] ?? m.label}
                <br />
                <small>{m.about}</small>
              </span>
              <Check />
            </button>
          ))}
          {!options && <p className={styles.note}>Loading the moves…</p>}
        </div>
      </div>

      <div className={styles.grp} style={{ marginTop: 0 }}>
        <b id={`fr-${shot.shot_id}`}>Framing</b>
        <div className={styles.fr} role="radiogroup" aria-labelledby={`fr-${shot.shot_id}`}>
          {(["wide", "medium", "close"] as const).map((f) => (
            <button
              key={f}
              type="button"
              role="radio"
              aria-checked={shot.framing === f}
              className={styles.chip}
              disabled={busy || !framingOk(f) || shot.framing === "cutaway"}
              title={!framingOk(f) ? "A close-up needs a face: nobody speaks in this shot." : undefined}
              onClick={() => shot.framing !== f && apply({ framing: f })}
            >
              <Check />
              {FRAMING_NAMES[f]}
            </button>
          ))}
        </div>
        {!shot.can_close && <p className={styles.note}>No close-up here: nobody speaks in this shot, so there is no face to cut to.</p>}
        {shot.framing === "cutaway" && <p className={styles.note}>A cutaway is drawn when the film is rendered, so its framing and move are set then.</p>}
      </div>

      <div className={styles.acts} style={{ marginTop: 0 }}>
        {shot.overridden && (
          <button className="textlink" disabled={busy} onClick={() => apply({ reset: true })}>
            Back to the planned camera
          </button>
        )}
        {hasFilm &&
          (shot.redraw ? (
            <span className="meta">Will be drawn again when you apply.</span>
          ) : (
            <button className="textlink" disabled={busy} onClick={() => void onRedraw()}>
              Draw this shot again
            </button>
          ))}
        {busy && (
          <span className="meta" role="status">
            Saving…
          </span>
        )}
      </div>
    </div>
  );
}

/** Where a scene sits and whether it stays. */
function SceneActions({
  number,
  count,
  sceneId,
  onOp,
  onChecked,
}: {
  number: number;
  count: number;
  sceneId: string;
  onOp: (op: string, params: Record<string, unknown>) => Promise<void>;
  onChecked: () => void;
}) {
  const [sure, setSure] = useState(false);
  return (
    <div className={styles.edc} role="group" aria-label={`Scene ${number}`}>
      <div className={styles.stack}>
        <button type="button" className="btn small" onClick={onChecked}>
          Looks right: fold this scene
        </button>
        {count > 1 && number > 1 && (
          <button type="button" className="btn small" onClick={() => void onOp("move_scene", { scene: sceneId, to: number - 1 })}>
            Move earlier
          </button>
        )}
        {count > 1 && number < count && (
          <button type="button" className="btn small" onClick={() => void onOp("move_scene", { scene: sceneId, to: number + 1 })}>
            Move later
          </button>
        )}
      </div>
      {count > 1 &&
        (sure ? (
          <div className={styles.confirm} role="group" aria-label={`Remove scene ${number}?`}>
            <p>Remove scene {number}? Its picture and lines go with it, and Go back can bring it back.</p>
            <div className={styles.acts} style={{ marginTop: 0 }}>
              <button
                type="button"
                className="btn small"
                onClick={() => {
                  setSure(false);
                  void onOp("remove_scene", { scene: sceneId });
                }}
              >
                Yes, remove it
              </button>
              <button type="button" className="textlink" onClick={() => setSure(false)}>
                Keep it
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className="textlink" onClick={() => setSure(true)}>
            Remove scene
          </button>
        ))}
    </div>
  );
}

/** The scene's title, what the camera sees, and its lines. A new description redraws the picture. */
function SceneWords({ card, onSave }: { card: Card; onSave: (edit: SceneEdit) => Promise<void> }) {
  const [title, setTitle] = useState(card.title);
  const [visual, setVisual] = useState(card.visual_prompt ?? "");
  const [lines, setLines] = useState<Record<string, string>>(Object.fromEntries(card.lines.map((l) => [l.line_id, l.text])));
  const [saving, setSaving] = useState(false);
  const visualChanged = visual.trim() !== (card.visual_prompt ?? "").trim();
  const dirty =
    visualChanged || title.trim() !== card.title || card.lines.some((l) => (lines[l.line_id] ?? "").trim() !== l.text);

  async function save() {
    setSaving(true);
    const changed = Object.fromEntries(Object.entries(lines).filter(([id, text]) => text.trim() !== card.lines.find((l) => l.line_id === id)?.text));
    try {
      await onSave({
        ...(title.trim() !== card.title ? { title: title.trim() } : {}),
        ...(visualChanged ? { visual_prompt: visual.trim() } : {}),
        ...(Object.keys(changed).length ? { dialogue: changed } : {}),
      });
    } catch {
      /* the page shows why; the form stays with what was typed */
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.edc} role="group" aria-label="Edit the scene">
      <label className="field">
        <span className="label">Scene title</span>
        <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="field">
        <span className="label">What the camera sees</span>
        <textarea className="textarea" rows={3} value={visual} onChange={(e) => setVisual(e.target.value)} />
      </label>
      {card.lines.map((l) => (
        <label key={l.line_id} className="field">
          <span className="label">{l.character}</span>
          <textarea className="textarea" rows={2} value={lines[l.line_id] ?? ""} onChange={(e) => setLines((prev) => ({ ...prev, [l.line_id]: e.target.value }))} />
        </label>
      ))}
      <p className={styles.note} style={{ margin: 0 }}>
        {visualChanged ? "A new description redraws this scene's picture: one image." : "Changes to the words are free; nothing is redrawn."}
      </p>
      <div className={styles.acts} style={{ marginTop: 0 }}>
        <button className="btn primary small" onClick={save} disabled={saving || !dirty}>
          {saving ? (visualChanged ? "Redrawing" : "Saving") : "Save scene"}
        </button>
      </div>
    </div>
  );
}
