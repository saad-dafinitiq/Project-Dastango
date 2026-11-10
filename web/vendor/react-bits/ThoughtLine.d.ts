import type { CSSProperties } from "react";

export type ThoughtLineProps = {
  label?: string;
  doneLabel?: string;
  renderLabel?: (label: string) => React.ReactNode;
  glyph?: "sparkle" | "dot" | string;
  steps?: string[];
  collapsible?: boolean;
  collapseOnSettle?: boolean;
  color?: string;
  glyphColor?: string;
  fontSize?: number;
  breathPeriod?: number;
  breathDepth?: number;
  shimmer?: boolean;
  shimmerDuration?: number;
  settleDuration?: number;
  settleBlur?: number;
  working?: boolean;
  settleAfter?: number;
  elapsed?: number;
  showTimer?: boolean;
  onSettle?: () => void;
  className?: string;
  style?: CSSProperties;
};

declare const ThoughtLine: (props: ThoughtLineProps) => React.ReactElement;
export default ThoughtLine;
