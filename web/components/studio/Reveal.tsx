"use client";

import { createContext, useContext } from "react";

import AnimatedContent from "@/vendor/react-bits/AnimatedContent";

/** The element that scrolls, when it is not the page: a side panel gives its own, so its contents are revealed as that panel
 *  scrolls rather than as the window does. */
const Scroller = createContext<HTMLElement | null>(null);
export const RevealScroller = Scroller.Provider;

/** A block of content that rises into place as it scrolls into view (React Bits' Animated Content, wrapped): short and quick,
 *  because a panel is used, not watched. Reduced motion shows it at once. */
export function Reveal({
  children,
  delay = 0,
  distance = 24,
  className,
}: {
  children: React.ReactNode;
  delay?: number;
  distance?: number;
  className?: string;
}) {
  const container = useContext(Scroller);
  return (
    <AnimatedContent container={container ?? undefined} distance={distance} duration={0.5} delay={delay} threshold={0.04} className={className}>
      {children}
    </AnimatedContent>
  );
}
