import type { ReactNode } from "react";

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" } as const;
const wrap = (children: ReactNode) => (
  <svg viewBox="0 0 24 24" aria-hidden="true" {...S}>
    {children}
  </svg>
);

/** One glyph for each tab of the editing panels, named by the tab's id. */
export const TAB_ICONS: Record<string, ReactNode> = {
  change: wrap(<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z" />),
  sound: wrap(<path d="M4 10v4h3.5L12 18V6L7.5 10zM15.5 9a4 4 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11" />),
  look: wrap(<path d="M12 4a8 8 0 1 0 0 16 3 3 0 0 0 0-6 3 3 0 0 1 0-6 3 3 0 0 0 0-4zM7.5 11.5h.01M10 8h.01M15 8h.01" />),
  titles: wrap(<path d="M5 6V4.5h14V6M12 4.5V19.5M9 19.5h6" />),
  versions: wrap(<path d="M4 12a8 8 0 1 0 2.5-5.8M4 4v4h4M12 8v4.5l3 1.5" />),
  camera: wrap(<path d="M3 8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM15 11l6-3v8l-6-3" />),
  transition: wrap(<path d="M3 7h5l2 5-2 5H3M21 7h-5l-2 5 2 5h5" />),
  words: wrap(<path d="M4 20l1-4L16.5 4.5a2 2 0 0 1 3 3L8 19zM14 7l3 3" />),
  scene: wrap(<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />),
};

export const FALLBACK_ICON = wrap(<circle cx="12" cy="12" r="3" />);
