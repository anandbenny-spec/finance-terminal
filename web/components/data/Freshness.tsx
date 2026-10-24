// Freshness (Design 01 §1.8, §4.2): "Data to 17 Oct 2026 · checked 20 Oct, 06:41 IST" in Plex Mono, and "Update
// delayed" (clock, orange) when a feeding task is behind: the last good data stays. Built by panelFreshness from
// api.task_freshness for the panel's own tasks (lib/feeds.ts); visitors never see task names or errors.
import { StateNote } from "@/components/data/StateNote";
import type { PanelFreshness } from "@/lib/shell/freshness";

export function Freshness({ freshness }: { freshness: PanelFreshness }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      {freshness.delayed && <StateNote kind="delayed">Update delayed</StateNote>}
      {freshness.text && <span className="font-mono">{freshness.text}</span>}
    </span>
  );
}
