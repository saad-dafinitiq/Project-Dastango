"use client";

import { useRef, useState } from "react";

import styles from "./inspector.module.css";

export type InspectorTab = { id: string; label: string; content: React.ReactNode };

type Props = {
  tabs: InspectorTab[];
  /** Always in view above the tabs: what a change in progress is doing, so it is seen whichever tab it was made from. */
  status?: (activeTab: string) => React.ReactNode;
  /** Controlled: the tab the parent wants shown (the sheet opens on Transition when its chip was pressed). */
  value?: string;
  onValueChange?: (id: string) => void;
  /** What is being edited, above the tabs. */
  title?: string;
  subtitle?: string;
  /** On a narrow screen the panel is a bottom sheet that is shown only once something was chosen. */
  sheet?: { open: boolean; onClose: () => void };
  /** Room kept under the panel for a bar fixed to the bottom of the window (the render bar). */
  bottomGap?: number;
  /** Something is being changed: the controls wait (they are inert, not just dimmed). */
  busy?: boolean;
};

/** The editing controls beside the film: one tab at a time, so a change is made and watched without scrolling. */
export function Inspector({ tabs, status, value, onValueChange, title, subtitle, sheet, bottomGap, busy }: Props) {
  const [own, setOwn] = useState(tabs[0]!.id);
  const wanted = value ?? own;
  const active = tabs.some((t) => t.id === wanted) ? wanted : tabs[0]!.id;
  const setActive = (id: string) => {
    setOwn(id);
    onValueChange?.(id);
  };
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const at = Math.max(0, tabs.findIndex((t) => t.id === active));

  function key(e: React.KeyboardEvent, i: number) {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    const jump = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : null;
    if (!step && jump === null) return;
    e.preventDefault();
    const next = jump ?? (i + step + tabs.length) % tabs.length;
    setActive(tabs[next]!.id);
    refs.current[next]?.focus();
  }

  return (
    <aside
      className={`${styles.inspector} ${sheet ? styles.sheet : ""} ${sheet?.open ? styles.sheetOpen : ""}`}
      style={bottomGap != null ? ({ "--insp-bottom": `${bottomGap}px` } as React.CSSProperties) : undefined}
      aria-label="Change the film"
    >
      {title && (
        <header className={styles.head}>
          <div>
            <b>{title}</b>
            {subtitle && <span>{subtitle}</span>}
          </div>
          {sheet && (
            <button type="button" className={styles.close} onClick={sheet.onClose} aria-label="Close">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          )}
        </header>
      )}
      {status?.(active)}
      <div className={styles.tabs} role="tablist" aria-label="What to change">
        {tabs.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            id={`insp-tab-${t.id}`}
            role="tab"
            type="button"
            aria-selected={t.id === active}
            aria-controls={`insp-panel-${t.id}`}
            tabIndex={i === at ? 0 : -1}
            className={styles.tab}
            onClick={() => setActive(t.id)}
            onKeyDown={(e) => key(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.body} id={`insp-panel-${active}`} role="tabpanel" aria-labelledby={`insp-tab-${active}`} tabIndex={0} inert={busy} aria-busy={busy || undefined}>
        {tabs.find((t) => t.id === active)?.content}
      </div>
    </aside>
  );
}
