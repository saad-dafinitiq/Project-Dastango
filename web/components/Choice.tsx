"use client";

import { useRef } from "react";

import { FORMATS, STYLES, type FormatId, type StyleId } from "@/lib/format";

import styles from "./choice.module.css";

const Check = () => (
  <svg className={styles.ck} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="m3 8.5 3.2 3.2L13 5" />
  </svg>
);

/** The shape of a frame, drawn as an outline: wide, tall or square. */
function Glyph({ id }: { id: FormatId }) {
  const r = id === "16:9" ? { x: 2, y: 5.5, w: 18, h: 11 } : id === "9:16" ? { x: 5.5, y: 2, w: 11, h: 18 } : { x: 3.5, y: 3.5, w: 15, h: 15 };
  return (
    <svg className={styles.glyph} viewBox="0 0 22 22" aria-hidden="true">
      <rect x={r.x} y={r.y} width={r.w} height={r.h} rx="1.6" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

type Option<T extends string | number> = { id: T; title: string; sub: string; glyph?: FormatId };

/** One radio group: a ring and a check on the chosen one, never colour alone. Arrow keys move. */
export function ChoiceGroup<T extends string | number>({
  label,
  options,
  value,
  onChange,
  disabled,
  compact,
}: {
  label: string;
  options: Option<T>[];
  value: T;
  onChange: (v: T) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const at = Math.max(0, options.findIndex((o) => o.id === value));

  function key(e: React.KeyboardEvent, i: number) {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (i + step + options.length) % options.length;
    onChange(options[next]!.id);
    refs.current[next]?.focus();
  }

  return (
    <div className={styles.group}>
      <b className={styles.label} id={`g-${label}`}>
        {label}
      </b>
      <div className={styles.seg} role="radiogroup" aria-labelledby={`g-${label}`}>
        {options.map((o, i) => (
          <button
            key={o.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={value === o.id}
            tabIndex={i === at ? 0 : -1}
            disabled={disabled}
            className={`${styles.opt} ${compact ? styles.compact : ""}`}
            onClick={() => onChange(o.id)}
            onKeyDown={(e) => key(e, i)}
          >
            {o.glyph && <Glyph id={o.glyph} />}
            <span>
              {o.title}
              {o.sub && <small>{o.sub}</small>}
            </span>
            <Check />
          </button>
        ))}
      </div>
    </div>
  );
}

export function FormatChoice({ value, onChange, disabled, compact }: { value: FormatId; onChange: (v: FormatId) => void; disabled?: boolean; compact?: boolean }) {
  return (
    <ChoiceGroup
      label="Format"
      value={value}
      onChange={onChange}
      disabled={disabled}
      compact={compact}
      options={FORMATS.map((f) => ({ id: f.id, title: f.id, sub: compact ? f.short : f.audience, glyph: f.id }))}
    />
  );
}

export function StyleChoice({ value, onChange, disabled, compact }: { value: StyleId; onChange: (v: StyleId) => void; disabled?: boolean; compact?: boolean }) {
  return (
    <ChoiceGroup
      label="Style"
      value={value}
      onChange={onChange}
      disabled={disabled}
      compact={compact}
      options={STYLES.map((s) => ({ id: s.id, title: s.label, sub: s.about }))}
    />
  );
}
