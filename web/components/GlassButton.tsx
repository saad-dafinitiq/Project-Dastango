"use client";

import SpecularButton, { type SpecularButtonProps } from "@/vendor/react-bits/SpecularButton";

import { useIsDark } from "./useTheme";

/** A button whose rim catches a moving light that follows the pointer (React Bits' Specular Button, wrapped). The light
 *  is drawn in WebGL with concrete colours, so the palette's are given here; with no WebGL it is a plain button.
 *  `lit` turns on the light that circles the rim on its own: used to say "this is ready". */
export function GlassButton({ lit = false, ...rest }: SpecularButtonProps & { lit?: boolean }) {
  const dark = useIsDark();
  return (
    <SpecularButton
      tint={dark ? "#ffffff" : "#352118"}
      tintOpacity={dark ? 0.06 : 0.05}
      lineColor={dark ? "#ffffff" : "#923d17"}
      baseColor={dark ? "#7d8088" : "#8a7060"}
      textColor="var(--fg)"
      autoAnimate={lit}
      speed={0.5}
      idleIntensity={0.45}
      {...rest}
    />
  );
}
