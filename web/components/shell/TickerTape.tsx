// TickerTape (Spec 01 §4.1; Design 01 §1.7, §4.1): a static end-of-day strip, 32 px a row (below lg one row that scrolls sideways; from lg as many rows as its items need), Plex Mono 12. The EOD tag,
// then each published series: name (ink-2) · value (ink-1) · Delta, with hairline separators. Hover or focus shows
// the full name, the date and the source line (with the index owner's copyright line for the four US indices).
// Values read from api.series_latest (F24); nothing is shown that the database didn't return.
import type { ReactNode } from "react";
import { Delta } from "@/components/data/Delta";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import { TapeScroller } from "@/components/shell/TapeScroller";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { readFreshness, readTape } from "@/lib/queries/shell";
import { FEEDS } from "@/lib/feeds";
import { taskState } from "@/lib/shell/freshness";
import { buildTape, type TapeItem } from "@/lib/shell/tape";

// The focus ring sits inside the cell: the strip clips anything outside its rows. One cell is one 32 px row high
// (--tape-row-h); the cells never wrap inside themselves, the list wraps them from lg (1 Nov 2026, #227).
const CELL = "flex h-(--tape-row-h) items-center border-r border-line focus-visible:-outline-offset-2";

function Tip({ trigger, children }: { trigger: ReactNode; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{trigger}</TooltipTrigger>
      <TooltipContent side="bottom" align="start">
        {children}
      </TooltipContent>
    </Tooltip>
  );
}

function Item({ item }: { item: TapeItem }) {
  return (
    <li className="h-(--tape-row-h)">
      <Tip
        trigger={
          <span tabIndex={0} className={`${CELL} gap-2 px-3.5`}>
            <span aria-hidden className="flex items-center gap-2">
              <span className="text-ink-2">{item.label}</span>
              <span className="text-ink-1">{item.value}</span>
              {item.change && <Delta change={item.change} />}
              {item.month && <span className="text-ink-3">{item.month}</span>}
              {item.qualifier && <Qualifier>{item.qualifier}</Qualifier>}
            </span>
            <span className="sr-only">{item.description}</span>
          </span>
        }
      >
        <p className="font-medium">{item.name}</p>
        <p className="text-ink-2">{item.qualifier ? `Monthly series: the value is for ${item.month}` : item.date}</p>
        {item.sourceLines.map((line) => (
          <p key={line} className="text-ink-3">
            {line}
          </p>
        ))}
      </Tip>
    </li>
  );
}

/** The strip itself, from already-read data (kept apart from the reads so it can be rendered in tests). */
export function TapeView({ items, delayed }: { items: TapeItem[] | null; delayed: boolean }) {
  const eod = (
    <Tip trigger={<span tabIndex={0} className={`${CELL} px-2.5 text-ink-3`}>EOD</span>}>
      End-of-day values; each item shows its date
    </Tip>
  );
  return (
    <div className="flex min-h-(--tape-row-h) shrink-0 border-b border-line bg-panel font-mono text-meta">
      <TapeScroller>
        {items !== null && items.length > 0 ? (
          // One wrapping list: the EOD tag, any "Update delayed" note and the items share the flow, so no column
          // narrows the rows (a third row only at the narrowest desktop widths).
          <ul
            aria-label="End-of-day market values"
            className="flex h-(--tape-row-h) w-max items-center whitespace-nowrap lg:h-auto lg:w-auto lg:flex-wrap"
          >
            <li className="h-(--tape-row-h)">{eod}</li>
            {delayed && (
              <li className="h-(--tape-row-h)">
                <span className={`${CELL} px-3`}>
                  <StateNote kind="delayed">Update delayed</StateNote>
                </span>
              </li>
            )}
            {items.map((item) => (
              <Item key={item.key} item={item} />
            ))}
          </ul>
        ) : (
          <div className="flex h-(--tape-row-h) w-max items-center whitespace-nowrap">
            {eod}
            <span className="px-3">
              {items === null ? (
                <StateNote kind="error">Couldn&apos;t load market values.</StateNote>
              ) : (
                <StateNote kind="unavailable">No end-of-day values published yet.</StateNote>
              )}
            </span>
          </div>
        )}
      </TapeScroller>
    </div>
  );
}

export async function TickerTape() {
  const [tape, freshness] = await Promise.all([readTape(), readFreshness()]);
  const items = tape.ok ? buildTape(tape.latest, tape.series) : null;
  // "Update delayed" only when the tape's task is known to be behind; an unread freshness claims nothing either way.
  const delayed =
    freshness.ok &&
    FEEDS.tickerTape.some((task) => taskState(freshness.tasks.find((r) => r.task_key === task)) !== "current");
  return <TapeView items={items} delayed={delayed} />;
}
