"use client";

import { useMemo } from "react";

import ThoughtLine from "@/vendor/react-bits/ThoughtLine";

import { describe } from "@/lib/copy";
import type { ProgressEvent } from "@/lib/types";

/** What the plan is doing, in the words the creator reads, and what it has done so far: React Bits' Thought Line. While
 *  the plan runs the line breathes and the steps below it tick off; when it is done it settles to "Planned in 14.2 s" and
 *  folds the steps away, still there to open. */
export function PlanThought({
  events,
  working,
  elapsed,
  onStop,
}: {
  events: ProgressEvent[];
  working: boolean;
  elapsed: number | null;
  onStop?: () => void;
}) {
  const steps = useMemo(() => {
    const out: string[] = [];
    for (const ev of events) {
      const text = describe(ev);
      if (text && out[out.length - 1] !== text) out.push(text);
    }
    return out;
  }, [events]);
  const label = steps[steps.length - 1] ?? "Starting";

  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 18, flexWrap: "wrap" }}>
      <ThoughtLine
        label={label}
        doneLabel="Planned in"
        steps={steps}
        working={working}
        elapsed={elapsed ?? undefined}
        fontSize={18}
        color="var(--fg)"
        glyphColor="var(--ac-t)"
      />
      {working && onStop && (
        <button className="btn quiet small" onClick={onStop}>
          Stop
        </button>
      )}
    </div>
  );
}
