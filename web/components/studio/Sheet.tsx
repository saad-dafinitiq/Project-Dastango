"use client";

import { useState } from "react";

import type { SceneEdit, ShotChange } from "@/lib/api";
import type { Card, CardShot, CastMember, ShotOptions } from "@/lib/types";

import styles from "./sheet.module.css";
import { FRAMING_NAMES, MOVE_NAMES, MoveGlyph, ShotPicture, mmss } from "./shots";

const Check = () => (
  <svg className={styles.ck} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="m3 8.5 3.2 3.2L13 5" />
  </svg>
);
const Pencil = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4" />
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
  onSaveScene: (sceneId: string, edit: SceneEdit) => Promise<void>;
  onShot: (sceneId: string, shotId: string, change: ShotChange) => Promise<void>;
};

type Open = { sceneId: string; shotId: string | null } | null;

export function Sheet({ cards, cast, planning, editable, hasFilm, options, onSaveScene, onShot }: Props) {
  const [open, setOpen] = useState<Open>(null);
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const portraits = Object.fromEntries(cast.filter((m) => m.portrait_url).map((m) => [m.character_id, m.portrait_url as string]));

  const toggle = (id: string, on: boolean) =>
    setFolded((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  return (
    <div>
      {cards.map((card, i) => {
        const number = i + 1;
        const isOpen = open?.sceneId === card.scene_id;
        const isFolded = folded.has(card.scene_id) && !isOpen;

        if (isFolded) {
          return (
            <section key={card.scene_id} className={`${styles.scene} ${styles.folded}`} aria-label={`Scene ${number}, checked`}>
              <div className={styles.rail}>
                <h2>{number}</h2>
                <div>
                  <h3>{card.title}</h3>
                  <p>
                    {card.shots?.length ?? 0} shots
                  </p>
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
                <button className={`textlink ${styles.reopen} ${styles.mini}`} style={{ marginLeft: "auto" }} onClick={() => toggle(card.scene_id, false)}>
                  Reopen
                </button>
              </div>
            </section>
          );
        }

        const shots = card.shots ?? [];
        const linesById = new Map(card.lines.map((l) => [l.line_id, l]));
        const selected = isOpen ? shots.find((s) => s.shot_id === open?.shotId) : undefined;

        return (
          <section key={card.scene_id} className={styles.scene} aria-label={`Scene ${number}: ${card.title}`}>
            <div className={styles.rail}>
              <h2>{String(number).padStart(2, "0")}</h2>
              <div>
                <h3>{card.title}</h3>
                <p>{[card.tone, card.setting].filter(Boolean).join(" · ")}</p>
                {editable && (
                  <div className={styles.railActions}>
                    <button
                      className="textlink"
                      onClick={() => setOpen(isOpen && open?.shotId === null ? null : { sceneId: card.scene_id, shotId: null })}
                    >
                      Edit scene words and picture
                    </button>
                    <button className="textlink" onClick={() => toggle(card.scene_id, true)}>
                      Looks right
                    </button>
                  </div>
                )}
              </div>
            </div>

            <ol className={styles.shots} aria-label={`${shots.length} shots in scene ${number}`}>
              {shots.length === 0 && (
                <li className={styles.shot}>
                  <div className={`${styles.frame} ${card.preview_url ? "" : styles.pending}`}>
                    {card.preview_url && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={card.preview_url} alt={card.setting || card.title} />
                    )}
                  </div>
                </li>
              )}
              {shots.map((shot, j) => {
                const on = isOpen && open?.shotId === shot.shot_id;
                const said = shot.line_ids.map((id) => linesById.get(id)).filter(Boolean);
                return (
                  <li key={shot.shot_id} className={`${styles.shot} rise`} style={{ "--delay": `${(i * 3 + j) * 50}ms` } as React.CSSProperties} aria-current={on ? "true" : undefined}>
                    <div className={`${styles.frame} ${card.preview_url || shot.framing === "cutaway" ? "" : styles.pending}`}>
                      <ShotPicture shot={shot} preview={card.preview_url} portrait={shot.character_id ? portraits[shot.character_id] : undefined} />
                      <span className={styles.tc}>{mmss(shot.start_ms)}</span>
                    </div>
                    <div className={styles.line}>
                      <div>
                        <b>{shot.framing === "close" && shot.character ? shot.character : FRAMING_NAMES[shot.framing] ?? shot.framing}</b>
                        <div className={`${styles.mv} ${shot.overridden ? styles.own : ""}`}>
                          {MOVE_NAMES[shot.move_id] ?? shot.move}
                          {shot.overridden ? " · your choice" : ""}
                        </div>
                      </div>
                      {editable && (
                        <button
                          className={styles.edit}
                          aria-expanded={on}
                          onClick={() => setOpen(on ? null : { sceneId: card.scene_id, shotId: shot.shot_id })}
                        >
                          <Pencil />
                          {on ? "Editing" : "Edit shot"}
                        </button>
                      )}
                    </div>
                    {said.length > 0 ? (
                      said.map((l) => (
                        <p key={l!.line_id} className={styles.say}>
                          <b>
                            {l!.character}
                            {l!.voice ? ` · ${l!.voice}` : ""}
                          </b>
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

            {isOpen && selected && (
              <ShotEditor
                card={card}
                shot={selected}
                index={shots.indexOf(selected)}
                options={options}
                hasFilm={hasFilm}
                portraits={portraits}
                onShot={(change) => onShot(card.scene_id, selected.shot_id, change)}
                onClose={() => setOpen(null)}
              />
            )}
            {isOpen && !selected && open?.shotId === null && (
              <SceneWords card={card} onSave={(edit) => onSaveScene(card.scene_id, edit)} onClose={() => setOpen(null)} />
            )}
          </section>
        );
      })}
      {planning && cards.length === 0 && (
        <p className={styles.status} aria-live="polite">
          <span className="pip" /> Writing the script
        </p>
      )}
    </div>
  );
}

/** One shot's camera move and framing, applied as soon as they are chosen. */
function ShotEditor({
  card,
  shot,
  index,
  options,
  hasFilm,
  portraits,
  onShot,
  onClose,
}: {
  card: Card;
  shot: CardShot;
  index: number;
  options: ShotOptions | null;
  hasFilm: boolean;
  portraits: Record<string, string>;
  onShot: (change: ShotChange) => Promise<void>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const shots = card.shots ?? [];
  const total = Math.max(1, shots.reduce((n, s) => n + (s.end_ms - s.start_ms), 0));
  const said = shot.line_ids.map((id) => card.lines.find((l) => l.line_id === id)).filter(Boolean);
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
    <div className={styles.ed} role="group" aria-label={`Edit shot ${index + 1}`}>
      <div>
        <h3>
          Shot {index + 1}, {FRAMING_NAMES[shot.framing] ?? shot.framing}
          <span className={styles.editing}>
            <Check /> Editing
          </span>
        </h3>
        <p className={styles.sub}>
          Changes apply to this shot only.{" "}
          {hasFilm ? "The film is cut again around it, which takes a little while but draws no new pictures." : "Nothing is drawn."}
        </p>

        <div className={styles.grp}>
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

        <div className={styles.grp}>
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

        <div className={styles.acts}>
          <button className="btn primary small" onClick={onClose}>
            Done
          </button>
          {shot.overridden && (
            <button className="textlink" disabled={busy} onClick={() => apply({ reset: true })}>
              Back to the planned camera
            </button>
          )}
          {busy && (
            <span className="meta" role="status">
              Saving…
            </span>
          )}
        </div>
      </div>

      <div>
        <div className={styles.preview} aria-label="Preview of the move">
          <div className={styles.moving} data-move={shot.move_id}>
            <ShotPicture shot={shot} preview={card.preview_url} portrait={shot.character_id ? portraits[shot.character_id] : undefined} />
          </div>
        </div>
        <div className={styles.tline} aria-hidden="true">
          {shots.map((s) => (
            <i key={s.shot_id} data-on={s.shot_id === shot.shot_id} style={{ flexGrow: Math.max(1, s.end_ms - s.start_ms), flexBasis: 0 }} />
          ))}
        </div>
        <div className={styles.tlabels} aria-hidden="true">
          {shots.map((s) => (
            <span key={s.shot_id} data-on={s.shot_id === shot.shot_id} style={{ flexGrow: Math.max(1, s.end_ms - s.start_ms), flexBasis: 0 }}>
              {FRAMING_NAMES[s.framing]}
            </span>
          ))}
        </div>
        <p className="meta" style={{ marginTop: 10 }}>
          {mmss(shot.start_ms)} to {mmss(shot.end_ms)} of about {mmss(total)}. The cut times follow the lines and are exact once the voices are recorded.
        </p>
        {said.length > 0 && (
          <p className={styles.sayline}>
            <b>{said.map((l) => l!.character).join(", ")}</b>
            {said.map((l) => l!.text).join(" ")}
          </p>
        )}
      </div>
    </div>
  );
}

/** The scene's title, what the camera sees, and its lines. A new description redraws the picture. */
function SceneWords({ card, onSave, onClose }: { card: Card; onSave: (edit: SceneEdit) => Promise<void>; onClose: () => void }) {
  const [title, setTitle] = useState(card.title);
  const [visual, setVisual] = useState(card.visual_prompt ?? "");
  const [lines, setLines] = useState<Record<string, string>>(Object.fromEntries(card.lines.map((l) => [l.line_id, l.text])));
  const [saving, setSaving] = useState(false);
  const visualChanged = visual.trim() !== (card.visual_prompt ?? "").trim();

  async function save() {
    setSaving(true);
    const changed = Object.fromEntries(
      Object.entries(lines).filter(([id, text]) => text.trim() !== card.lines.find((l) => l.line_id === id)?.text),
    );
    try {
      await onSave({
        ...(title.trim() !== card.title ? { title: title.trim() } : {}),
        ...(visualChanged ? { visual_prompt: visual.trim() } : {}),
        ...(Object.keys(changed).length ? { dialogue: changed } : {}),
      });
      onClose();
    } catch {
      /* the page shows why; the form stays open with what was typed */
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.ed} role="group" aria-label="Edit the scene">
      <div className={styles.words}>
        <h3>
          Scene words and picture
          <span className={styles.editing}>
            <Check /> Editing
          </span>
        </h3>
        <label className="field">
          <span className="label">Scene title</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="field">
          <span className="label">What the camera sees</span>
          <textarea className="textarea" rows={3} value={visual} onChange={(e) => setVisual(e.target.value)} />
        </label>
      </div>
      <div className={styles.words}>
        {card.lines.map((l) => (
          <label key={l.line_id} className="field">
            <span className="label">{l.character}</span>
            <textarea
              className="textarea"
              rows={2}
              value={lines[l.line_id] ?? ""}
              onChange={(e) => setLines((prev) => ({ ...prev, [l.line_id]: e.target.value }))}
            />
          </label>
        ))}
        <p className={styles.note}>
          {visualChanged ? "A new description redraws this scene's picture: one image." : "Changes to the words are free; nothing is redrawn."}
        </p>
        <div className={styles.acts} style={{ marginTop: 0 }}>
          <button className="btn primary small" onClick={save} disabled={saving}>
            {saving ? (visualChanged ? "Redrawing" : "Saving") : "Save scene"}
          </button>
          <button className="textlink" onClick={onClose} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
