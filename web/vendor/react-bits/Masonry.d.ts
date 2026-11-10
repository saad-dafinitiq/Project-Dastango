import type { CSSProperties, ComponentType, MouseEvent } from "react";

export type MasonryItem = {
  id: string | number;
  img: string;
  width?: number;
  height?: number;
  span?: number;
  title?: string;
  description?: string;
  alt?: string;
  url?: string;
};

export type MasonryProps<T extends MasonryItem = MasonryItem> = {
  items?: T[];
  columns?: "auto" | number;
  columnWidth?: number;
  gap?: number;
  radius?: number;
  animateFrom?: "bottom" | "top" | "left" | "right" | "center" | "random" | "none";
  stagger?: number;
  duration?: number;
  bounce?: number;
  blurToFocus?: boolean;
  revealOnScroll?: boolean;
  hoverEffect?: "zoom" | "lift" | "tilt" | "none";
  focusOnHover?: boolean;
  captions?: "hover" | "always" | "none";
  parallax?: number;
  lightbox?: boolean;
  draggable?: boolean;
  onItemClick?: (item: T, index: number, event: MouseEvent) => void;
  onReorder?: (items: T[]) => void;
  className?: string;
  style?: CSSProperties;
};

declare const Masonry: <T extends MasonryItem = MasonryItem>(props: MasonryProps<T>) => React.ReactElement;
export default Masonry;
