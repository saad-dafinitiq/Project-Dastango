"use client";

import ShinyText from "@/vendor/react-bits/ShinyText";

import { useIsDark } from "./useTheme";

/** Type that glows softly and is crossed by a slow sheen, for the two lines that open Create. React Bits' Shiny Text,
 *  wrapped. It paints with canvas colours, which cannot be CSS variables, so the palette's own values are given here:
 *  the page's text colour as the base, and a lighter (dark mode) or an ember (light mode) sheen over it. */
export function GlowText({ children, glow = 0.5, speed = 5, delay = 1.5 }: { children: string; glow?: number; speed?: number; delay?: number }) {
  const dark = useIsDark();
  return (
    <ShinyText
      color={dark ? "#eef0f4" : "#352118"}
      shineColor={dark ? "#ffffff" : "#b4501f"}
      glow={dark ? glow : glow * 0.6}
      speed={speed}
      delay={delay}
      shineWidth={60}
      style={{ display: "block", textWrap: "balance" }}
    >
      {children}
    </ShinyText>
  );
}
