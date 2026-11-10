"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import Masonry, { type MasonryItem } from "@/vendor/react-bits/Masonry";

import { statusLine } from "@/lib/films";

import type { LibraryFilm } from "./FilmGrid";
import styles from "./films.module.css";

export type Ratio = "16:9" | "9:16" | "1:1";

/** One folder per shape of film, in the order a creator meets them. */
export const FOLDERS: { ratio: Ratio; name: string; width: number; height: number; column: number }[] = [
  { ratio: "16:9", name: "Widescreen", width: 1600, height: 900, column: 300 },
  { ratio: "9:16", name: "Vertical", width: 900, height: 1600, column: 210 },
  { ratio: "1:1", name: "Square", width: 1000, height: 1000, column: 250 },
];

/** Shown where a film has no picture yet (a storyboard still being drawn): a quiet tile, not a broken image. */
const NO_FRAME = `data:image/svg+xml,${encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='9'><rect width='16' height='9' fill='#2a2b2f'/></svg>",
)}`;

type FilmItem = MasonryItem & { film: LibraryFilm };

const Chevron = ({ open }: { open: boolean }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform .18s" }}>
    <path d="m7 10 5 5 5-5" />
  </svg>
);
const FolderGlyph = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
);
const PinGlyph = ({ on }: { on: boolean }) => (
  <svg viewBox="0 0 24 24" fill={on ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M9 4h6l-1 6 3 3v2H7v-2l3-3zM12 15v5" />
  </svg>
);

/** Masonry draws its own cards, so there is nowhere inside it to put a button. Each card's wrapper carries the film's id
 *  (`data-id`); this finds them again whenever the layout changes, so a pin can be portalled in beside the card. */
function useSlots(root: React.RefObject<HTMLElement | null>, ids: string[]) {
  const [slots, setSlots] = useState<Record<string, HTMLElement>>({});
  const key = ids.join("|");
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const wanted = new Set(key.split("|"));
    let frame = 0;
    const find = () => {
      frame = 0;
      const next: Record<string, HTMLElement> = {};
      el.querySelectorAll<HTMLElement>("[data-id]").forEach((node) => {
        if (node.dataset.id && wanted.has(node.dataset.id)) next[node.dataset.id] = node;
      });
      setSlots((was) => {
        const same = Object.keys(next).length === Object.keys(was).length && Object.entries(next).every(([k, v]) => was[k] === v);
        return same ? was : next;
      });
    };
    const soon = () => {
      if (!frame) frame = requestAnimationFrame(find);
    };
    soon();
    const watch = new MutationObserver(soon);
    watch.observe(el, { childList: true, subtree: true });
    return () => {
      watch.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [root, key]);
  return slots;
}

/** The films of one shape, in a folder that opens and closes. Touch a film to open it; the pin on its corner puts it on Create. */
export function FilmFolder({
  folder,
  films,
  pinned,
  onPin,
  heading: Heading = "h2",
}: {
  folder: (typeof FOLDERS)[number];
  films: LibraryFilm[];
  pinned: (film: LibraryFilm) => boolean;
  onPin: (film: LibraryFilm, on: boolean) => void;
  heading?: "h2" | "h3";
}) {
  const router = useRouter();
  const [open, setOpen] = useState(true);
  const body = useRef<HTMLDivElement>(null);
  const items = useMemo<FilmItem[]>(
    () =>
      films.map((f) => ({
        id: f.project_id,
        img: f.poster_url ?? NO_FRAME,
        width: folder.width,
        height: folder.height,
        title: f.title || "Untitled",
        description: statusLine(f).text,
        film: f,
      })),
    [films, folder],
  );
  const slots = useSlots(body, items.map((i) => String(i.id)));
  const count = films.filter(pinned).length;

  return (
    <section className={styles.folder} aria-labelledby={`folder-${folder.ratio}`}>
      <Heading className={styles.folderHead} id={`folder-${folder.ratio}`}>
        <button type="button" className={styles.folderBtn} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <FolderGlyph />
          <span className={styles.folderName}>{folder.name}</span>
          <span className={styles.folderRatio}>{folder.ratio}</span>
          <span className={styles.folderCount}>
            {films.length} {films.length === 1 ? "film" : "films"}
            {count ? ` · ${count} pinned` : ""}
          </span>
          <Chevron open={open} />
        </button>
      </Heading>
      {open && (
        <div ref={body} className={styles.folderBody}>
          <Masonry<FilmItem>
            items={items}
            columnWidth={folder.column}
            gap={14}
            radius={10}
            animateFrom="bottom"
            hoverEffect="lift"
            captions="always"
            lightbox={false}
            draggable={false}
            onItemClick={(item) => router.push(`/studio/?id=${encodeURIComponent(item.film.project_id)}`)}
          />
          {items.map((item) => {
            const slot = slots[String(item.id)];
            if (!slot) return null;
            const on = pinned(item.film);
            return createPortal(
              <button
                type="button"
                className={`${styles.pin} ${on ? styles.pinned : ""}`}
                aria-pressed={on}
                aria-label={on ? `Unpin ${item.title} from Create` : `Pin ${item.title} to Create`}
                title={on ? "Pinned: shown on Create. Click to unpin" : "Pin to show on Create"}
                onClick={() => onPin(item.film, !on)}
              >
                <PinGlyph on={on} />
              </button>,
              slot,
              String(item.id),
            );
          })}
        </div>
      )}
    </section>
  );
}
