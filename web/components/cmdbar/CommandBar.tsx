"use client";

// The command bar (K-07, #585; Design 01 §4.6, §1.11, §5.5; Spec 07 §7), rendered only while `site.command_bar` is published
// (CommandBarSlot). From md: an input in the top bar (36 px, raised, the ⌘K / Ctrl K tag) that opens a popover under it, at most
// 720 px wide; below md: a search button that opens the same box as a full-screen sheet. ⌘K / Ctrl K focuses it, Esc closes it,
// arrows move (cmdk's combobox). Its states, in Design 01 §4.6's words: Empty (example chips), Typing (plain-search groups,
// then "Ask: '…'"), Working (one status line, announced politely), then the answer (AnswerView).
//
// Plain search never calls /api. An ask goes to `/api/cmdbar/ask`, which answers the paused state while the AI is switched off
// (`cmdbar_ai_enabled = false` until go-live: Spec 07 §3), so the box shows "AI questions are paused for now; search still
// works." and nothing else. An example chip shows its precomputed answer: no model call, no limit counted.
//
// A document answer's citation chips (K-08, #586) open their passage in a popover, or a bottom sheet on touch and in the phone
// sheet, portaled out of the box: a press in one doesn't close the box, Esc closes only that layer, and an answer's links and
// chips keep Enter and the arrows (cmdk's list would take them). On phones the box is the full-screen sheet, the answer under
// the input, with no sideways scroll at 320 px.
import { Search, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { AnswerView } from "@/components/cmdbar/AnswerView";
import { useSearch } from "@/components/cmdbar/useSearch";
import { StateNote } from "@/components/data/StateNote";
import { Command, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import {
  FIRST_STATUS,
  PLACEHOLDER,
  SEARCH_PAGE,
  TRY,
  askLabel,
  askQuestion,
  fetchExamples,
  groupHits,
  hitOf,
  isShortcut,
  keepAnswerKeys,
  pageHits,
  shortcutLabel,
  type Answer,
  type Example,
  type SearchHit,
} from "@/lib/cmdbar";
import { formatCount } from "@/lib/format";

/** The longest question the route takes (`ft.api.cmdbar`: 500 characters once whitespace is collapsed). */
const MAX_QUESTION = 500;
/** The hint while no example is checked yet (K-09 fills them): the Empty state is never blank. */
const EMPTY_HINT = "Type to search meetings and companies, or ask a question about the data.";

let examplesOnce: Promise<Example[]> | null = null;
/** The checked examples, read once per page load, on the first open (the endpoint is cached for five minutes). */
function loadExamples(): Promise<Example[]> {
  examplesOnce ??= fetchExamples();
  return examplesOnce;
}

function CommandBox({
  variant,
  inputRef,
  shortcut,
  onNavigated,
}: {
  variant: "inline" | "sheet";
  inputRef?: RefObject<HTMLInputElement | null>;
  shortcut: string;
  onNavigated: () => void;
}) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(variant === "sheet");
  const [status, setStatus] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [examples, setExamples] = useState<Example[]>([]);
  const askAbort = useRef<AbortController | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  /** The last pointer press inside the box, in React's tree: a chip's popover or sheet is portaled out of the box's DOM. */
  const pressInside = useRef<Event | null>(null);
  const { term, result, loading, loadMore } = useSearch(answer || status ? "" : q);
  /** When the answer on screen arrived: its times are written against it (lib/format.ts). */
  const [now, setNow] = useState(() => new Date().toISOString());

  // The examples, once the box is first opened.
  useEffect(() => {
    if (!open) return;
    let live = true;
    loadExamples().then((list) => {
      if (live) setExamples(list);
    });
    return () => {
      live = false;
    };
  }, [open]);

  // The inline popover closes on a pointer press outside the box. A press in a citation's popover or sheet (K-08) is inside:
  // those are portaled to the body, but React's events still pass through the box (`onPointerDownCapture` below), which runs
  // before this listener does, so "Open in document" and "Official source ↗" stay clickable.
  useEffect(() => {
    if (variant !== "inline" || !open) return;
    const onDown = (e: PointerEvent) => {
      if (pressInside.current === e) return;
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [variant, open]);

  useEffect(() => () => askAbort.current?.abort(), []);

  const reset = useCallback(() => {
    askAbort.current?.abort();
    setStatus(null);
    setAnswer(null);
  }, []);

  /** Leaving for a page: the popover closes (the sheet closes through its parent). */
  const close = useCallback(() => {
    setOpen(variant === "sheet");
    onNavigated();
  }, [variant, onNavigated]);

  const go = useCallback(
    (href: string) => {
      close();
      router.push(href);
    },
    [router, close],
  );

  const ask = useCallback((question: string) => {
    askAbort.current?.abort();
    const ac = new AbortController();
    askAbort.current = ac;
    setAnswer(null);
    setStatus(FIRST_STATUS);
    askQuestion(question, { onStatus: (line) => !ac.signal.aborted && setStatus(line), signal: ac.signal })
      .then((a) => {
        if (ac.signal.aborted) return;
        setStatus(null);
        setNow(new Date().toISOString());
        setAnswer(a);
      })
      .catch(() => {});
  }, []);

  const hits: SearchHit[] = useMemo(() => {
    const fromIndex = (result?.rows ?? []).map(hitOf).filter((h): h is SearchHit => h !== null);
    return [...pageHits(term), ...fromIndex];
  }, [term, result]);
  const groups = groupHits(hits);
  const question = q.trim();
  const canAsk = question.length >= 2;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // Esc in an open citation popover or sheet closes only that layer: Radix takes the key first and marks it handled.
    if (e.key === "Escape" && variant === "inline" && open && !e.nativeEvent.defaultPrevented) {
      e.preventDefault();
      setOpen(false);
    }
  };

  let body: React.ReactNode;
  if (status && !answer) {
    body = (
      <p aria-hidden className="px-3 py-2 text-ui text-ink-2">
        {status}
      </p>
    );
  } else if (answer) {
    body = (
      // The answer's own links, chips and "Show SQL" keep Enter and the arrows (keepAnswerKeys).
      <div className="px-3 py-2" onKeyDown={keepAnswerKeys}>
        <AnswerView answer={answer} now={now} onNavigate={close} inSheet={variant === "sheet"} />
      </div>
    );
  } else if (!question) {
    body =
      examples.length > 0 ? (
        <CommandGroup heading={TRY} className="[&_[cmdk-group-items]]:flex [&_[cmdk-group-items]]:flex-wrap [&_[cmdk-group-items]]:gap-1.5">
          {examples.map((ex) => (
            <CommandItem
              key={ex.question}
              value={`example:${ex.question}`}
              onSelect={() => {
                setQ(ex.question);
                askAbort.current?.abort();
                setStatus(null);
                setNow(new Date().toISOString());
                setAnswer(ex.answer);
              }}
              className="min-h-7 rounded-sm border border-line-input bg-raised px-2 text-data"
            >
              {ex.question}
            </CommandItem>
          ))}
        </CommandGroup>
      ) : (
        <p className="px-3 py-2 text-ui text-ink-2">{EMPTY_HINT}</p>
      );
  } else {
    const shown = result?.rows.length ?? 0;
    body = (
      <>
        {groups.map(({ group, hits: list }) => (
          <CommandGroup key={group} heading={group}>
            {list.map((h) => (
              <CommandItem key={h.key} value={h.key} onSelect={() => go(h.href)}>
                <span className="min-w-0 flex-1 truncate">{h.title}</span>
                {h.detail && <span className="shrink-0 text-meta text-ink-3">{h.detail}</span>}
              </CommandItem>
            ))}
          </CommandGroup>
        ))}
        {result && result.status === "ok" && result.total > shown && (
          <CommandGroup>
            <CommandItem value="more" onSelect={loadMore} disabled={result.more} className="text-ink-2">
              {result.more ? "Loading more…" : `Show ${formatCount(Math.min(SEARCH_PAGE, result.total - shown))} more`}
              <span className="ml-auto text-meta text-ink-3">
                {formatCount(shown)} of {formatCount(result.total)}
              </span>
            </CommandItem>
          </CommandGroup>
        )}
        {result?.status === "error" && (
          <p className="px-3 py-1.5">
            <StateNote kind="error">Couldn&apos;t search meetings and companies just now. Try again.</StateNote>
          </p>
        )}
        {term && !loading && result?.status === "ok" && hits.length === 0 && (
          <p className="px-3 py-1.5 text-ui text-ink-2">No page, company or meeting matches.</p>
        )}
        {canAsk && (
          <CommandGroup>
            <CommandItem value="ask" onSelect={() => ask(question)}>
              <Sparkles aria-hidden className="size-3.5 shrink-0 text-highlight" />
              <span className="min-w-0 flex-1 truncate">{askLabel(question)}</span>
            </CommandItem>
          </CommandGroup>
        )}
      </>
    );
  }

  const panelOpen = variant === "sheet" || open;
  return (
    <Command
      ref={rootRef}
      onPointerDownCapture={(e) => {
        pressInside.current = e.nativeEvent;
      }}
      shouldFilter={false}
      loop
      label="Search and ask"
      onKeyDown={onKeyDown}
      className={variant === "inline" ? "relative" : "h-full"}
    >
      <div
        className={
          variant === "inline"
            ? "flex h-9 items-center gap-2 rounded-md border border-line-input bg-raised px-2.5"
            : "flex h-12 shrink-0 items-center gap-2 border-b border-line-strong px-3 pr-12"
        }
      >
        <Search aria-hidden className="size-4 shrink-0 text-ink-3" />
        <CommandInput
          ref={inputRef}
          value={q}
          maxLength={MAX_QUESTION}
          placeholder={PLACEHOLDER}
          onValueChange={(v) => {
            setQ(v);
            reset();
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
        />
        {variant === "inline" && (
          <kbd className="shrink-0 rounded-sm border border-line px-1 font-mono text-meta text-ink-3">{shortcut}</kbd>
        )}
      </div>
      {/* One polite line for screen readers: the working stages (Design 01 §4.6). */}
      <p role="status" aria-live="polite" className="sr-only">
        {status ?? ""}
      </p>
      {panelOpen && (
        <div
          className={
            variant === "inline"
              ? "absolute inset-x-0 top-full z-40 mt-1 max-h-(--nav-max-h) overflow-hidden rounded-md border border-line-strong bg-raised lg:inset-x-auto lg:left-1/2 lg:w-180 lg:-translate-x-1/2"
              : "min-h-0 flex-1"
          }
        >
          <CommandList className={variant === "inline" ? "max-h-150 py-1" : "h-full py-1"}>{body}</CommandList>
        </div>
      )}
    </Command>
  );
}

/** The box, its mobile button and sheet, and the one shortcut. */
export function CommandBar() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const [shortcut, setShortcut] = useState("Ctrl K");
  const inlineRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // After the first render, so the server's markup and the browser's first render agree (no hydration mismatch).
    const platform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ?? navigator.platform;
    const label = shortcutLabel(platform);
    if (label !== "Ctrl K") queueMicrotask(() => setShortcut(label));
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (!isShortcut(e)) return;
      e.preventDefault();
      if (window.matchMedia("(min-width: 768px)").matches) inlineRef.current?.focus();
      else setSheetOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <div className="hidden w-full md:block">
        <CommandBox variant="inline" inputRef={inlineRef} shortcut={shortcut} onNavigated={() => inlineRef.current?.blur()} />
      </div>
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetTrigger asChild>
          <button
            type="button"
            aria-label="Search and ask"
            className="grid size-8 shrink-0 place-items-center rounded-md border border-line-input text-ink-2 hover:bg-raised hover:text-ink-1 md:hidden"
          >
            <Search aria-hidden className="size-4" />
          </button>
        </SheetTrigger>
        <SheetContent side="bottom" className="h-dvh" closeLabel="Close search">
          <SheetTitle className="sr-only">Search and ask</SheetTitle>
          <CommandBox variant="sheet" shortcut={shortcut} onNavigated={() => setSheetOpen(false)} />
        </SheetContent>
      </Sheet>
    </>
  );
}
