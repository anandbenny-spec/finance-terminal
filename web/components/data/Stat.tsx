// Stat and StatRow (Design 01 §4.3, §1.15): label (meta, ink-2) · value (figure; its unit in ink-2 at ui size) ·
// a context line · an as-of line (ink-3). A stat about one market carries the 3 px market edge on its top. StatRow
// places them with hairline dividers, four across from md and two per row below.
import { cn } from "cn";
import type { ReactNode } from "react";

const EDGE = { US: "border-t-3 border-t-series-1", IN: "border-t-3 border-t-series-2" } as const;

export function Stat({
  label,
  value,
  unit,
  context,
  asOf,
  market,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: string;
  context?: ReactNode;
  asOf?: ReactNode;
  market?: "US" | "IN";
}) {
  return (
    <div className={cn("min-w-0 bg-panel p-3", market && EDGE[market])}>
      <div className="text-meta text-ink-2">{label}</div>
      <div className="mt-1 mb-0.5 text-figure font-semibold text-ink-1">
        <span className="whitespace-nowrap">{value}</span>
        {unit && <span className="ml-1 text-ui font-normal text-ink-2">{unit}</span>}
      </div>
      {context && <div className="text-data text-ink-1">{context}</div>}
      {asOf && <div className="mt-0.5 text-meta text-ink-3">{asOf}</div>}
    </div>
  );
}

export function StatRow({ children }: { children: ReactNode }) {
  return (
    // Hairline dividers from the 1 px gaps over the line colour: right for both layouts, with no edge cases.
    <div className="grid grid-cols-2 gap-px bg-line md:grid-cols-4">
      {children}
    </div>
  );
}
