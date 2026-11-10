import type { CSSProperties, ComponentType } from "react";

export type GridMotionItem = string | { src: string; alt?: string };

export type GridMotionProps = {
  items?: GridMotionItem[];
  rows?: number;
  aspectRatio?: number;
  gap?: number;
  radius?: number;
  angle?: number;
  tilt?: number;
  speed?: number;
  direction?: "alternate" | "left" | "right";
  parallax?: number;
  spotlight?: number;
  dim?: number;
  grayscale?: boolean;
  fade?: number;
  tileColor?: string;
  textColor?: string;
  theme?: "dark" | "light";
  className?: string;
  style?: CSSProperties;
};

declare const GridMotion: ComponentType<GridMotionProps>;
export default GridMotion;
