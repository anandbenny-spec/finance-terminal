"use client";

// CitationChip and its passage (Design 01 §1.10, §4.5): "[1]" in amber, a 24 × 24 px hit area, reachable by
// keyboard in reading order. Activating it (click, tap, Enter) opens the quoted passage: on a fine pointer a popover
// (max 360 px), on touch a bottom sheet. Opening on activation rather than hover keeps the passage's links usable by
// keyboard and touch alike. The passage has a 2 px amber rule, the document's title, "Open in document" and, where the source may be
// linked, "Official source ↗" (never for the RBI: Anand, 24 Oct 2026). The quote is rebuilt from our stored text by
// api.ai_citations, never from model output.
import { useSyncExternalStore } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import type { Citation } from "@/lib/policy/ai";

const COARSE = "(pointer: coarse)";

function subscribe(onChange: () => void) {
  const mq = window.matchMedia(COARSE);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

function useCoarsePointer(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia(COARSE).matches, () => false);
}

function Passage({ citation }: { citation: Citation }) {
  return (
    <>
      <blockquote className="mb-2.5 border-l-2 border-amber pl-2.5 text-ink-1">{citation.quote}</blockquote>
      {citation.documentTitle && <p className="text-meta text-ink-2">{citation.documentTitle}</p>}
      <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-meta">
        {citation.href && (
          <a href={citation.href} className="text-ink-1 underline">
            Open in document
          </a>
        )}
        {citation.officialUrl && (
          <a href={citation.officialUrl} className="text-ink-1 underline" rel="noreferrer" target="_blank">
            Official source ↗
          </a>
        )}
      </p>
    </>
  );
}

const CHIP =
  "inline-grid h-6 min-w-6 cursor-pointer place-items-center rounded-sm px-0.5 align-baseline text-meta font-medium text-amber hover:bg-amber-wash";

export function CitationChip({ citation }: { citation: Citation }) {
  const coarse = useCoarsePointer();
  const chip = (
    <button type="button" className={CHIP} aria-label={`Source ${citation.no}: show the cited passage`}>
      [{citation.no}]
    </button>
  );
  if (coarse) {
    return (
      <Sheet>
        <SheetTrigger asChild>{chip}</SheetTrigger>
        <SheetContent side="bottom" className="p-4 pt-10 text-data">
          <SheetTitle className="sr-only">Cited passage {citation.no}</SheetTitle>
          <Passage citation={citation} />
        </SheetContent>
      </Sheet>
    );
  }
  return (
    <Popover>
      <PopoverTrigger asChild>{chip}</PopoverTrigger>
      <PopoverContent aria-label={`Cited passage ${citation.no}`} align="start">
        <Passage citation={citation} />
      </PopoverContent>
    </Popover>
  );
}

/** Chips after the last word of a claim, joined to it so a chip never sits alone on a line (Design 01 §4.5). */
export function Cited({ text, citations }: { text: string; citations: Citation[] }) {
  const cut = text.lastIndexOf(" ");
  const [head, last] = cut > 0 ? [text.slice(0, cut + 1), text.slice(cut + 1)] : ["", text];
  return (
    <>
      {head}
      <span className="whitespace-nowrap">
        {last}
        {citations.map((c) => (
          <span key={c.no}>
            {" "}
            <CitationChip citation={c} />
          </span>
        ))}
      </span>
    </>
  );
}
