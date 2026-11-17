"use client";

// The command bar's answer states (K-07, #585; Design 01 §4.6, word for word; Spec 07 §4, §7): a data answer (its summary marked
// "AI summary", a dense DataTable, "Show SQL" with a copy button, a footer with the view it read and the time taken), a page
// found, Can't answer (with suggested pages), Not found (documents), Limit reached (also the paused state) and the client's own
// error state. Never a raw error, a run id or a task name (CLAUDE.md §4). A document answer's sentences carry CitationChips
// (K-08, #586): a popover on a fine pointer, a bottom sheet on touch, and never a link to RBI's pages (CLAUDE.md §1).
import Link from "next/link";
import { Check, Copy, Sparkles } from "lucide-react";
import { useState } from "react";
import { Cited } from "@/components/ai/CitationChip";
import { DataTable, type Column } from "@/components/data/DataTable";
import { StateNote } from "@/components/data/StateNote";
import {
  AI_SUMMARY,
  CANT_TEXT,
  CITED_DOCUMENTS,
  ERROR_TEXT,
  NOT_FOUND_TEXT,
  PAUSED_TEXT,
  SUGGESTED_PAGES,
  documentAnswer,
  documentLine,
  documentPage,
  formatCell,
  isAiSummary,
  isFedDocument,
  rowMarket,
  type Answer,
  type Cell,
  type PageLink,
} from "@/lib/cmdbar";
import { formatDurationMs } from "@/lib/format";

/** The sparkle and "AI summary" (Design 01 §1.10): every AI line in the box carries it. */
export function AiMark() {
  return (
    <span className="inline-flex items-center gap-1.5 text-data font-medium text-ink-1">
      <Sparkles aria-hidden className="size-3.5 text-highlight" />
      {AI_SUMMARY}
    </span>
  );
}

function Pages({ pages, onNavigate }: { pages: readonly PageLink[]; onNavigate: () => void }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-ui">
      {pages.map((p) => (
        <li key={p.href}>
          <Link prefetch={false} href={p.href} onClick={onNavigate} className="inline-flex min-h-6 items-center text-ink-1 underline">
            {p.title}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          })
          .catch(() => {});
      }}
      className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line-input px-2 text-meta text-ink-2 hover:bg-raised hover:text-ink-1"
    >
      {copied ? <Check aria-hidden className="size-3.5" /> : <Copy aria-hidden className="size-3.5" />}
      {copied ? "Copied" : "Copy SQL"}
    </button>
  );
}

type Row = { i: number; cells: Cell[] };

function DataAnswer({ answer, now }: { answer: Answer; now: string }) {
  const rows: Row[] = answer.rows.map((cells, i) => ({ i, cells }));
  const columns: Column<Row>[] = answer.columns.map((c, j) => ({
    id: `c${j}`,
    header: c.name,
    align: c.kind === "number" ? "right" : "left",
    cell: (r: Row) => formatCell(r.cells[j], c.kind, rowMarket(answer.columns, r.cells), now),
  }));
  return (
    <div className="space-y-2">
      {isAiSummary(answer) ? (
        <div>
          <AiMark />
          <p className="mt-1 text-ui text-ink-1">{answer.summary}</p>
        </div>
      ) : (
        answer.summary && <p className="text-ui text-ink-2">{answer.summary}</p>
      )}
      {columns.length > 0 && (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => String(r.i)}
          caption="The rows the query returned"
          dense
          scroll
          empty={<StateNote kind="empty">The query returned no rows.</StateNote>}
        />
      )}
      {answer.sql && (
        <details className="rounded-md border border-line">
          <summary className="cursor-pointer px-2 py-1.5 text-ui text-ink-2 hover:text-ink-1">Show SQL</summary>
          <div className="space-y-2 border-t border-line p-2">
            <pre className="overflow-x-auto font-mono text-meta whitespace-pre-wrap text-ink-1">{answer.sql}</pre>
            <CopyButton text={answer.sql} />
          </div>
        </details>
      )}
      <p className="font-mono text-meta text-ink-3">
        {answer.view ? `${answer.view} · ` : ""}
        {formatDurationMs(answer.ms)}
      </p>
    </div>
  );
}

/**
 * A document answer (K-08, #586; Design 01 §1.10, §4.5, §4.6): each sentence with its `CitationChip`s (the popover on a fine
 * pointer, a bottom sheet on touch), then the documents they cite. A Fed document links to its official source; an RBI document
 * is named by its title, date and press-release number, never linked to RBI's pages (CLAUDE.md §1). Each title opens the
 * document on this site.
 */
function DocumentAnswer({
  lines,
  documents,
  ms,
  onNavigate,
  inSheet,
}: ReturnType<typeof documentAnswer> & { ms: number; onNavigate: () => void; inSheet: boolean }) {
  return (
    <div className="space-y-2">
      <AiMark />
      <ul className="space-y-1.5 text-ui break-words text-ink-1">
        {lines.map((l, i) => (
          <li key={i}>
            <Cited text={l.text} citations={l.citations} sheet={inSheet} />
          </li>
        ))}
      </ul>
      <div className="border-t border-line pt-2">
        <p className="text-meta text-ink-3">{CITED_DOCUMENTS}</p>
        <ul className="text-meta text-ink-2">
          {documents.map((d, i) => {
            const page = documentPage(d);
            const official = isFedDocument(d) ? d.source_url : null;
            return (
              <li key={i} className="flex min-h-6 flex-wrap items-center gap-x-3 break-words">
                {page ? (
                  <Link prefetch={false} href={page} onClick={onNavigate} className="min-w-0 py-1 text-ink-1 underline">
                    {documentLine(d)}
                  </Link>
                ) : (
                  <span className="min-w-0 py-1">{documentLine(d)}</span>
                )}
                {official && (
                  <a href={official} className="py-1 text-ink-1 underline" rel="noreferrer" target="_blank">
                    Official source ↗
                  </a>
                )}
              </li>
            );
          })}
        </ul>
      </div>
      <p className="font-mono text-meta text-ink-3">{formatDurationMs(ms)}</p>
    </div>
  );
}

/**
 * One answer, in the state the route (or the client) gave it. `inSheet`: the answer is in the full-screen sheet (phones), so its
 * citation chips open their passages as bottom sheets, above it, on any pointer.
 */
export function AnswerView({
  answer,
  now,
  onNavigate,
  inSheet = false,
}: {
  answer: Answer;
  now: string;
  onNavigate: () => void;
  inSheet?: boolean;
}) {
  switch (answer.state) {
    case "paused":
    case "limit":
      return <StateNote kind="unavailable">{PAUSED_TEXT}</StateNote>;
    case "error":
      return <StateNote kind="error">{ERROR_TEXT}</StateNote>;
    case "not_found":
      return <StateNote kind="empty">{NOT_FOUND_TEXT}</StateNote>;
    case "page":
      if (answer.pages.length > 0) {
        return (
          <div>
            <p className="text-ui text-ink-2">That&apos;s a page on this site:</p>
            <Pages pages={answer.pages.slice(0, 1)} onNavigate={onNavigate} />
          </div>
        );
      }
      break;
    case "answered":
      if (answer.route === "document") {
        const doc = documentAnswer(answer);
        // No sentence with a chip left: nothing uncited is shown, and the answer is the route's own "not found".
        if (doc.lines.length === 0) return <StateNote kind="empty">{NOT_FOUND_TEXT}</StateNote>;
        return <DocumentAnswer {...doc} ms={answer.ms} onNavigate={onNavigate} inSheet={inSheet} />;
      }
      if (answer.route === "data") return <DataAnswer answer={answer} now={now} />;
      break;
  }
  // Can't answer, and anything the box can't show as an answer: the same words, with pages to go to.
  return (
    <div>
      <StateNote kind="empty">{CANT_TEXT}</StateNote>
      <Pages pages={answer.pages.length > 0 ? answer.pages.slice(0, 3) : SUGGESTED_PAGES} onNavigate={onNavigate} />
    </div>
  );
}
