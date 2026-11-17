"use client";

// The command bar's answer states (K-07, #585; Design 01 §4.6, word for word; Spec 07 §4, §7): a data answer (its summary marked
// "AI summary", a dense DataTable, "Show SQL" with a copy button, a footer with the view it read and the time taken), a page
// found, Can't answer (with suggested pages), Not found (documents), Limit reached (also the paused state) and the client's own
// error state. Never a raw error, a run id or a task name (CLAUDE.md §4). A document answer's citation chips and phone sheet
// are K-08's (#586); until then its sentences carry plain [n] markers into the documents listed under them, with no links.
import Link from "next/link";
import { Check, Copy, Sparkles } from "lucide-react";
import { useState } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { StateNote } from "@/components/data/StateNote";
import {
  AI_SUMMARY,
  CANT_TEXT,
  ERROR_TEXT,
  NOT_FOUND_TEXT,
  PAUSED_TEXT,
  SUGGESTED_PAGES,
  formatCell,
  isAiSummary,
  rowMarket,
  type Answer,
  type Cell,
  type PageLink,
} from "@/lib/cmdbar";
import { formatDate, formatDurationMs } from "@/lib/format";

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
          <Link prefetch={false} href={p.href} onClick={onNavigate} className="text-ink-1 underline">
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

function DocumentAnswer({ answer }: { answer: Answer }) {
  return (
    <div className="space-y-2">
      <AiMark />
      <ul className="space-y-1.5 text-ui text-ink-1">
        {answer.citations.map((c, i) => (
          <li key={i}>
            {c.text}{" "}
            <span className="font-mono text-meta whitespace-nowrap text-ink-2">
              {[...new Set(c.refs.map((r) => r.document))].map((d) => `[${d + 1}]`).join("")}
            </span>
          </li>
        ))}
      </ul>
      <ol className="space-y-1 border-t border-line pt-2 text-meta text-ink-2">
        {answer.documents.map((d, i) => (
          <li key={i}>
            [{i + 1}] {d.bank} · {d.title}
            {d.date ? ` · ${formatDate(d.date)}` : ""}
            {d.press_release ? ` · Press release ${d.press_release}` : ""}
          </li>
        ))}
      </ol>
      <p className="font-mono text-meta text-ink-3">{formatDurationMs(answer.ms)}</p>
    </div>
  );
}

/** One answer, in the state the route (or the client) gave it. */
export function AnswerView({ answer, now, onNavigate }: { answer: Answer; now: string; onNavigate: () => void }) {
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
      if (answer.route === "document" && answer.citations.length > 0) return <DocumentAnswer answer={answer} />;
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
