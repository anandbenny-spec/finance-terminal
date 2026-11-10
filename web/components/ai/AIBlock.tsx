// AIBlock (Design 01 §1.10, §4.5): a sparkle icon and the block's kind ("AI summary", "What changed") with an info
// tooltip (the model and when it generated the text), then the bullets, each ending in its citation chips. Only
// published, cited outputs reach it (lib/policy/ai.ts drops any bullet without a verified citation). With `quotes` (the
// Calls tab's summary, #371) each bullet also shows its cited quotes under it, in citation order, as the passage shows them
// (a blockquote with a 2 px white rule) plus the PDF page link; nothing opens on hover and nothing is cut.
import { Info, Sparkles } from "lucide-react";
import { Cited } from "@/components/ai/CitationChip";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatCheckedTime } from "@/lib/format";
import type { AiBlockData, Citation } from "@/lib/policy/ai";

/** The quotes under one bullet: one blockquote per citation, in citation order, with its page link where the view has one. */
function BulletQuotes({ citations }: { citations: Citation[] }) {
  return (
    <div className="mt-1.5 space-y-1.5">
      {citations.map((c) => (
        <div key={c.no}>
          <blockquote className="border-l-2 border-highlight pl-2.5 text-data text-ink-1 break-words">
            <span className="sr-only">Source {c.no}: </span>
            {c.fullQuote ?? c.quote}
          </blockquote>
          {c.page && (
            <p className="mt-0.5 pl-2.5 text-meta">
              <a href={c.page.url} title={c.page.title} className="text-ink-1 underline" rel="noreferrer" target="_blank">
                {c.page.label} ↗
              </a>
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

export function AIBlock({
  block,
  now,
  dense = true,
  note,
  quotes = false,
}: {
  block: AiBlockData;
  now: string;
  dense?: boolean;
  note?: string;
  /** Show each bullet's cited quotes under it (default false: the chips alone, as module 1's blocks have them). */
  quotes?: boolean;
}) {
  const about = `${block.model} · generated ${formatCheckedTime(block.createdAt, now)}`;
  return (
    <div>
      <div className="flex items-center gap-1.5 text-data font-medium text-ink-1">
        <Sparkles aria-hidden className="size-3.5 text-highlight" />
        {block.label}
        <Tooltip>
          <TooltipTrigger asChild>
            <button type="button" className="grid size-6 place-items-center rounded-sm text-ink-3 hover:text-ink-1" aria-label={`About this AI text: ${about}`}>
              <Info aria-hidden className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent>{about}</TooltipContent>
        </Tooltip>
      </div>
      {note && <p className="mt-0.5 text-meta text-ink-2">{note}</p>}
      <ul className={`mt-1.5 list-disc space-y-1.5 pl-4.5 ${dense ? "text-ui" : "text-read"}`}>
        {block.bullets.map((b, i) => (
          <li key={i}>
            <Cited text={b.text} citations={b.citations} />
            {quotes && <BulletQuotes citations={b.citations} />}
          </li>
        ))}
      </ul>
    </div>
  );
}
