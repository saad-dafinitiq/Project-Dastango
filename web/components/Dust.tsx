"use client";

import Particles from "@/vendor/react-bits/Particles";

import { useIsDark } from "./useTheme";

/** A few slow points of light behind a screen, leaning toward the pointer (React Bits' Particles, wrapped; it replaced the
 *  CSS grain and specks). Sign-in leaves it out. Pale in dark mode with a spark of ember, brown in light mode, where a
 *  pale point would vanish; the colours are concrete because the WebGL cannot read the page's variables.
 *  `speed` and `opacity` are live: the plan screen races them while a script is written. */
export function Dust({ speed = 0.06, opacity = 0.9, contained = false }: { speed?: number; opacity?: number; contained?: boolean }) {
  const dark = useIsDark();
  return (
    <div className={`dust${contained ? " dust--contained" : ""}`} style={{ opacity }} aria-hidden="true">
      <Particles
        particleCount={140}
        particleSpread={11}
        speed={speed}
        particleColors={dark ? ["#ffffff", "#c9ccd6", "#e39a45"] : ["#352118", "#5d483c", "#923d17"]}
        lightMode={!dark}
        particleBaseSize={dark ? 110 : 150}
        sizeRandomness={1}
        glow={dark ? 0.5 : 0.25}
        twinkle={0.4}
        moveParticlesOnHover
        particleHoverFactor={0.8}
        clickBurst={false}
      />
    </div>
  );
}
