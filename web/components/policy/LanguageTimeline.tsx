"use client";

// "How the wording moved" (#509; Design 01 §1.16, Pass 2): one panel per bank, RBI first, each a table of key phrases
// (the stance, the rate decision, how inflation is described) by meeting, newest first. A cell is the bank's own sentence
// for that phrase, with the words new since the meeting before underlined as Key changes underlines them (the sentence's
// `<ins>` marks: no colour judgement), and a ¶ link to the paragraph in the meeting's Statement tab. An unchanged
// sentence is faded and says "Unchanged". Where no single sentence matches the row's rule the cell says so and why, never a
// guess. DataTable scrolls the meetings sideways inside the panel with the phrase column sticky (Design 01 §5.5).
import Link from "next/link";
import { useMemo, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { formatTableDate } from "@/lib/format";
import { gapNote, type BankTimeline, type SentenceCell, type TimelineCell, type TimelineColumn } from "@/lib/policy/timeline";

type Line = { key: string; label: string };

const HOW: Record<SentenceCell["how"], string> = { same: "Unchanged", changed: "Changed", added: "New sentence" };

function CellView({ cell, column, line }: { cell: TimelineCell | undefined; column: TimelineColumn; line: Line }) {
  if (!cell || cell.kind === "gap") {
    const note = cell ? gapNote(cell) : { title: "Not shown", reason: "This cell isn't stored." };
    return (
      <div className="w-72 py-2 text-meta text-ink-3">
        <p>{note.title}</p>
        <p>{note.reason}</p>
      </div>
    );
  }
  const faded = cell.how === "same";
  return (
    <div className="w-72 py-2">
      <p className={`break-words text-ui ${faded ? "text-ink-3" : "text-ink-1"}`}>
        {cell.parts.map((part, i) => (part.op === "ins" ? <ins key={i}>{part.text}</ins> : <span key={i}>{part.text}</span>))}
      </p>
      <p className="mt-1 text-meta text-ink-3">
        {HOW[cell.how]}
        {cell.para !== null && (
          <>
            {" · "}
            <Link
              href={`${column.href}#p-${cell.para}`}
              className="whitespace-nowrap font-mono underline hover:text-ink-1"
              aria-label={`${line.label}, ${formatTableDate(column.date)}: paragraph ${cell.para}`}
            >
              ¶{cell.para}
            </Link>
          </>
        )}
      </p>
    </div>
  );
}

export function LanguageTimelinePanel({ data, footer, refresh }: { data: BankTimeline; footer: ReactNode; refresh?: readonly string[] }) {
  const rbi = data.bank === "RBI";
  const title = `${data.bank}: how the wording moved`;
  const columns = useMemo<Column<Line>[]>(
    () => [
      { id: "phrase", header: "Phrase", alignTop: true, cell: (line) => <span className="block py-2 font-medium">{line.label}</span> },
      ...data.columns.map(
        (col): Column<Line> => ({
          id: col.slug,
          header: formatTableDate(col.date),
          wrapCell: true,
          alignTop: true,
          cell: (line) => <CellView cell={col.cells[line.key]} column={col} line={line} />,
        }),
      ),
    ],
    [data],
  );
  return (
    <Panel
      title={title}
      titleId={`panel-language-timeline-${rbi ? "rbi" : "fed"}`}
      qualifier={<span className="text-meta text-ink-3">Since 2019</span>}
      subtitle={
        rbi
          ? "How has the RBI worded its stance, its rate decision and inflation, meeting by meeting?"
          : "How has the Fed worded its decision and inflation, meeting by meeting?"
      }
      market={rbi ? "IN" : "US"}
      variant="flush"
      span={12}
      footer={footer}
      refresh={refresh}
    >
      {data.columns.length === 0 ? (
        <div className="p-3">
          <StateNote kind="unavailable">No decisions are on the site yet.</StateNote>
        </div>
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={data.rows}
            rowKey={(line) => line.key}
            caption={`${data.bank} key phrases by meeting, newest first; the bank's own sentence, with the words new since the meeting before underlined`}
            focusable
            empty={<StateNote kind="unavailable">No phrases are tracked yet.</StateNote>}
          />
          <p className="border-t border-line px-3 py-2 text-meta text-ink-3">
            Each cell is one sentence of the bank&apos;s own {rbi ? "resolution" : "statement"}. Underlined words are new since the meeting before; a faded
            sentence is unchanged. Where no single sentence fits a phrase, the cell says so.
          </p>
        </>
      )}
    </Panel>
  );
}
