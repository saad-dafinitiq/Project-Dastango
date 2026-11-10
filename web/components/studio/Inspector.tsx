"use client";

import { useState } from "react";

import StarBorder from "@/vendor/react-bits/StarBorder";

import { useIsDark } from "@/components/useTheme";

import { FloatingDock } from "./FloatingDock";
import { FALLBACK_ICON, TAB_ICONS } from "./icons";
import { RevealScroller } from "./Reveal";
import styles from "./inspector.module.css";

export type InspectorTab = { id: string; label: string; content: React.ReactNode };

type Props = {
  tabs: InspectorTab[];
  /** Always in view above the dock: what a change in progress is doing, so it is seen whichever tab it was made from. */
  status?: (activeTab: string) => React.ReactNode;
  /** Controlled: the tab the parent wants shown (the sheet opens on Transition when its chip was pressed). */
  value?: string;
  onValueChange?: (id: string) => void;
  /** What is being edited, above the dock. */
  title?: string;
  subtitle?: string;
  /** On a narrow screen the panel is a bottom sheet that is shown only once something was chosen. */
  sheet?: { open: boolean; onClose: () => void };
  /** Room kept under the panel for a bar fixed to the bottom of the window (the render bar). */
  bottomGap?: number;
  /** Something is being changed: the controls wait (they are inert, not just dimmed). */
  busy?: boolean;
  /** Show only the dock until an icon is chosen (the film's own panel); a chosen icon pressed again closes it. */
  startClosed?: boolean;
};

/** The editing controls beside the film: a dock of round icons, and under it the one panel that was chosen. A change is made and
 *  watched without scrolling, and the panel's contents rise into place as it scrolls. */
export function Inspector({ tabs, status, value, onValueChange, title, subtitle, sheet, bottomGap, busy, startClosed }: Props) {
  const dark = useIsDark();
  const [own, setOwn] = useState(tabs[0]!.id);
  const [open, setOpen] = useState(!startClosed);
  // The element the panel scrolls in, so what is inside is revealed as that scrolls (and not as the page does).
  const [scroller, setScroller] = useState<HTMLElement | null>(null);
  const wanted = value ?? own;
  const active = tabs.some((t) => t.id === wanted) ? wanted : tabs[0]!.id;

  function choose(id: string) {
    if (id === active && open && startClosed) {
      setOpen(false);
      return;
    }
    setOwn(id);
    onValueChange?.(id);
    setOpen(true);
  }

  return (
    <StarBorder
      as="aside"
      className={`${styles.inspector} ${sheet ? styles.sheet : ""} ${sheet?.open ? styles.sheetOpen : ""}`}
      style={bottomGap != null ? ({ "--insp-bottom": `${bottomGap}px` } as React.CSSProperties) : undefined}
      aria-label="Change the film"
      radius={14}
      thickness={1}
      duration={7}
      glow={0.45}
      theme={dark ? "dark" : "light"}
      color={dark ? "#ffffff" : "#923d17"}
      backgroundColor="var(--s1)"
      borderColor="var(--line)"
      textColor="var(--fg)"
    >
      <div className={styles.inner}>
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
        <FloatingDock
          label="What to change"
          items={tabs.map((t) => ({ id: t.id, label: t.label, icon: TAB_ICONS[t.id] ?? FALLBACK_ICON }))}
          value={open ? active : ""}
          onSelect={choose}
          idFor={(id) => `insp-tab-${id}`}
        />
        {open && (
          <div
            ref={setScroller}
            className={styles.body}
            id={`insp-panel-${active}`}
            role="tabpanel"
            aria-labelledby={`insp-tab-${active}`}
            tabIndex={0}
            inert={busy}
            aria-busy={busy || undefined}
          >
            {scroller && (
              <RevealScroller value={scroller}>
                <div key={active}>{tabs.find((t) => t.id === active)?.content}</div>
              </RevealScroller>
            )}
          </div>
        )}
      </div>
    </StarBorder>
  );
}
