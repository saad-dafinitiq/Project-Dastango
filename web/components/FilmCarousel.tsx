"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import CircularCarousel, { type CarouselItem } from "@/vendor/react-bits/CircularCarousel";

import type { LibraryFilm } from "./FilmGrid";
import { Dust } from "./Dust";
import { GlassButton } from "./GlassButton";
import styles from "./create.module.css";

/** `id` is the film to open; a demo has none and is only turned to the front when touched. */
type RingItem = CarouselItem & { id?: string };

/** A ring needs enough cards to look like one; fewer films are repeated round it. */
const MIN_CARDS = 8;
const MAX_CARDS = 10;

/** The shipped example frames, shown to someone with no film yet. */
const DEMOS: RingItem[] = [1, 2, 3, 4].map((n) => ({ src: `/examples/frame-${n}.jpg`, alt: "", title: `A Dastango film, example ${n}` }));

function shuffled<T>(list: T[]): T[] {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j]!, a[i]!];
  }
  return a;
}

/** Which films the ring shows: the films the creator has pinned (on the Films screen), and only those; else a random handful of
 *  their own films with a picture, chosen once per visit; else the examples. */
function pick(films: LibraryFilm[]): RingItem[] {
  const framed = films.filter((f) => f.poster_url);
  const pinned = framed.filter((f) => f.pinned);
  const chosen = pinned.length > 0 ? pinned : shuffled(framed);
  const own = chosen
    .slice(0, MAX_CARDS)
    .map((f) => ({ id: f.project_id, src: f.poster_url as string, alt: f.title || "Untitled", title: f.title || "Untitled" }));
  const base = own.length > 0 ? own : DEMOS;
  return base.length >= MIN_CARDS ? base : Array.from({ length: MIN_CARDS }, (_, i) => base[i % base.length]!);
}

/** Card width follows the screen: big enough to read a frame, small enough that the ring fits. */
function useCardWidth(): number {
  const [w, setW] = useState(340);
  useEffect(() => {
    const set = () => setW(Math.round(Math.min(520, Math.max(220, window.innerWidth * 0.31))));
    set();
    window.addEventListener("resize", set);
    return () => window.removeEventListener("resize", set);
  }, []);
  return w;
}

/** A ring of films that fills the screen once you scroll to it: nothing under it but the front film's name. Touch a film to
 *  open it. React Bits' Circular Carousel, wrapped. */
export function FilmCarousel({ films }: { films: LibraryFilm[] }) {
  const router = useRouter();
  const width = useCardWidth();
  // Chosen once per visit, so the ring does not reshuffle while it is turning.
  const items = useMemo(() => pick(films), [films]);
  const demo = !films.some((f) => f.poster_url);

  return (
    <section className={styles.reel} aria-labelledby="your-films">
      <div className={styles.reelDust}>
        <Dust contained />
      </div>
      <div className={styles.reelIn}>
        <div className={styles.rh}>
          <h2 id="your-films">{demo ? "Made with Dastango" : "Your films"}</h2>
        </div>

        <div className={styles.carousel}>
          <CircularCarousel<RingItem>
            items={items}
            preset="cylinder"
            intro="rise"
            cardWidth={width}
            aspectRatio={3 / 2}
            gap={22}
            speed={9}
            captions
            fadeColor="var(--bg)"
            cornerRadius={10}
            onItemClick={(item) => item.id && router.push(`/studio/?id=${encodeURIComponent(item.id)}`)}
          />
        </div>

        {!demo && (
          <p className={styles.reelActs}>
            <GlassButton size="md" radius={14} onClick={() => router.push("/films/")}>
              See all {films.length} films
            </GlassButton>
          </p>
        )}
      </div>
    </section>
  );
}
