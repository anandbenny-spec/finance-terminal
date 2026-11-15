"use client";

// The "Show all N meetings" row under a long /policy panel (#575; Anand, 15 Nov 2026, 17:15 IST): the panel draws the
// latest 12 meetings (lib/policy/show-latest.ts) and this button draws the rest, or goes back to the latest 12. A text
// button in the panel's footer row, in ink colours with the highlight only on focus, so no new hue. `controls` names the
// region or table it expands, for screen readers.
import { formatCount } from "@/lib/format";
import { showAllLabel } from "@/lib/policy/show-latest";

export function ShowAllMeetings({
  total,
  trimmed,
  controls,
  onToggle,
}: {
  total: number;
  trimmed: boolean;
  controls: string;
  onToggle: () => void;
}) {
  return (
    <div className="border-t border-line px-3 py-2">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!trimmed}
        aria-controls={controls}
        className="inline-flex min-h-6 items-center rounded-md text-meta text-ink-2 underline hover:text-ink-1"
      >
        {showAllLabel(total, trimmed, (n) => formatCount(n))}
      </button>
    </div>
  );
}
