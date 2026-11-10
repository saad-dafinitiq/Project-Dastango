"use client";

import GridMotion from "@/vendor/react-bits/GridMotion";

import styles from "./create.module.css";

/** The slow field of frames behind the Create box: the creator's own films once there are enough of them,
 *  the shipped examples until then. Rows drift in opposite directions and lean toward the pointer. The tiles
 *  are dimmed toward the page colour (--grid-dim, set per palette) rather than covered by an overlay, so
 *  the headline and the box stay readable in both modes. React Bits' Grid Motion, wrapped, not edited. */
export function CreateBackdrop({ pictures }: { pictures: string[] }) {
  return (
    <div className={styles.stage} aria-hidden="true">
      <GridMotion
        items={pictures}
        rows={5}
        aspectRatio={16 / 9}
        gap={14}
        radius={10}
        angle={-8}
        speed={14}
        parallax={0.45}
        spotlight={0.55}
        fade={0.6}
        tileColor="var(--s1)"
        style={{ "--gm-dim": "var(--grid-dim)" } as React.CSSProperties}
      />
    </div>
  );
}
