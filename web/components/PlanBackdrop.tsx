"use client";

import { useEffect, useRef, useState } from "react";

import LightTunnel from "@/vendor/react-bits/LightTunnel";

import { Dust } from "./Dust";

import { useIsDark } from "./useTheme";

const RISE_S = 1.6; // the grain races, then the tunnel opens
const FALL_S = 1.6; // and closes again, more slowly than it opened, the moment there is something to read

/** How strong the tunnel gets at most: a backdrop, not a picture. */
const MAX_OPACITY = 0.4;
/** The title and the thought line sit at the top left: the strands are kept off them, so nothing is read through them. */
const CLEAR = "linear-gradient(to right, transparent 0%, transparent 28%, #000 60%), linear-gradient(to bottom, transparent 0%, transparent 24%, #000 52%)";

const smooth = (t: number) => t * t * (3 - 2 * t);

/** The page's particles, and behind them, while a plan is being written, a tunnel of light (React Bits' Light Tunnel).
 *  The particles speed up the moment the plan starts, the tunnel opens out of them and runs faster as it goes, and when the
 *  script arrives the tunnel fades and hands back to the slow particles. With reduced motion the tunnel stands still. */
export function PlanBackdrop({ active, onBusy }: { active: boolean; onBusy?: (busy: boolean) => void }) {
  const dark = useIsDark();
  const [level, setLevel] = useState(0);
  const at = useRef(0);

  useEffect(() => {
    const goal = active ? 1 : 0;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const step = dt / (goal > at.current ? RISE_S : FALL_S);
      at.current = goal > at.current ? Math.min(goal, at.current + step) : Math.max(goal, at.current - step);
      setLevel(at.current);
      if (at.current !== goal) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [active]);

  // Said once when the tunnel appears and once when it has gone, so the page can hold back what would be drawn over it.
  const busy = level > 0.02;
  const said = useRef(false);
  useEffect(() => {
    if (said.current !== busy) {
      said.current = busy;
      onBusy?.(busy);
    }
  }, [busy, onBusy]);

  const eased = smooth(level);

  return (
    <>
      <Dust speed={0.06 + 0.5 * level} opacity={0.9 * (1 - 0.5 * eased)} />
      {level > 0.01 && (
        <div style={{ position: "fixed", inset: 0, zIndex: -1, pointerEvents: "none", opacity: MAX_OPACITY * eased, WebkitMaskImage: CLEAR, maskImage: CLEAR, WebkitMaskComposite: "source-in", maskComposite: "intersect" }} aria-hidden="true">
          <LightTunnel
            cableColor={dark ? "#c3c8d4" : "#5d483c"}
            pulseColor={dark ? "#e39a45" : "#923d17"}
            lightMode={!dark}
            speed={0.025 + 0.42 * level * level}
            pulseSpeed={0.75 + 1.25 * level}
            centerX={0.45}
            size={0.85}
            brightness={dark ? 1.2 : 0.6}
            pulseDensity={0.7}
            glow={1.3}
            mouseInteraction={false}
            clickSurge={false}
          />
        </div>
      )}
    </>
  );
}
