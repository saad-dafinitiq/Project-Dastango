"use client";

import { AnimatePresence, motion, useMotionValue, useSpring, useTransform, type MotionValue } from "motion/react";
import { useRef, useState } from "react";

import styles from "./dock.module.css";

export type DockItem = { id: string; label: string; icon: React.ReactNode };

/** A row of round icons that swell as the pointer comes near, each naming itself on hover or focus. The behaviour of Aceternity
 *  UI's Floating Dock (the springs and the distance-to-size mapping), written here with plain CSS and as a tab list: the chosen one
 *  is ringed and dotted (not colour alone) and the arrow keys move between them. */
export function FloatingDock({
  items,
  value,
  onSelect,
  label,
  idFor,
}: {
  items: DockItem[];
  value: string;
  onSelect: (id: string) => void;
  label: string;
  idFor: (id: string) => string;
}) {
  const mouseX = useMotionValue(Infinity);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const at = Math.max(0, items.findIndex((i) => i.id === value));

  function key(e: React.KeyboardEvent, i: number) {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    const jump = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : null;
    if (!step && jump === null) return;
    e.preventDefault();
    const next = jump ?? (i + step + items.length) % items.length;
    onSelect(items[next]!.id);
    refs.current[next]?.focus();
  }

  return (
    <motion.div className={styles.dock} role="tablist" aria-label={label} onMouseMove={(e) => mouseX.set(e.pageX)} onMouseLeave={() => mouseX.set(Infinity)}>
      {items.map((item, i) => (
        <DockIcon
          key={item.id}
          item={item}
          mouseX={mouseX}
          on={item.id === value}
          tabIndex={i === at ? 0 : -1}
          id={idFor(item.id)}
          setRef={(el) => {
            refs.current[i] = el;
          }}
          onClick={() => onSelect(item.id)}
          onKeyDown={(e) => key(e, i)}
        />
      ))}
    </motion.div>
  );
}

function DockIcon({
  item,
  mouseX,
  on,
  tabIndex,
  id,
  setRef,
  onClick,
  onKeyDown,
}: {
  item: DockItem;
  mouseX: MotionValue<number>;
  on: boolean;
  tabIndex: number;
  id: string;
  setRef: (el: HTMLButtonElement | null) => void;
  onClick: () => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [named, setNamed] = useState(false);
  const distance = useTransform(mouseX, (x) => {
    const b = box.current?.getBoundingClientRect() ?? { x: 0, width: 0 };
    return x - b.x - b.width / 2;
  });
  const size = useSpring(useTransform(distance, [-110, 0, 110], [44, 62, 44]), { mass: 0.1, stiffness: 150, damping: 12 });
  const glyph = useSpring(useTransform(distance, [-110, 0, 110], [20, 28, 20]), { mass: 0.1, stiffness: 150, damping: 12 });

  return (
    <motion.div ref={box} style={{ width: size, height: size }} className={styles.cell}>
      <AnimatePresence>
        {named && (
          <motion.span className={styles.name} initial={{ opacity: 0, y: 8, x: "-50%" }} animate={{ opacity: 1, y: 0, x: "-50%" }} exit={{ opacity: 0, y: 2, x: "-50%" }}>
            {item.label}
          </motion.span>
        )}
      </AnimatePresence>
      <button
        ref={setRef}
        id={id}
        role="tab"
        type="button"
        aria-selected={on}
        aria-label={item.label}
        tabIndex={tabIndex}
        className={`${styles.icon} ${on ? styles.on : ""}`}
        onClick={onClick}
        onKeyDown={onKeyDown}
        onMouseEnter={() => setNamed(true)}
        onMouseLeave={() => setNamed(false)}
        onFocus={() => setNamed(true)}
        onBlur={() => setNamed(false)}
      >
        <motion.span style={{ width: glyph, height: glyph }} className={styles.glyph}>
          {item.icon}
        </motion.span>
      </button>
      {on && <i className={styles.dot} aria-hidden="true" />}
    </motion.div>
  );
}
