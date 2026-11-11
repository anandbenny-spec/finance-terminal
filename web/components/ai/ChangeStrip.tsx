"use client";

// ChangeStrip (Design 01 §1.16 layer 1, §4.5; Spec 01 §4.2; Pass 1b, #89): "How much each statement changed". One row
// per bank; per meeting, a bar of the share of the statement's words added (above the line, --ins-ink) and removed
// (below, --del-ink) against the statement before it, drawn as two Lightweight Charts histograms (TimeSeriesChart:
// one library for time series). Shares, not counts, so a 400-word FOMC statement and a multi-page RBI resolution
// share a scale; both rows share ±60% (more only when a bar needs it). Deterministic: the stored word counts, no AI.
//
// Takes its rows as props and reads nothing, so /policy and the front page both place it (lib/policy/changestrip.ts
// builds the rows from lib/queries/policy.ts's readStatementChanges). A canvas has no focusable bars, so the keyboard
// does what the pointer does one bar at a time: each row is a focusable group where Left and Right choose a meeting
// and Enter opens its What changed tab; the readout above the row (a live region) says what the chosen bar holds and
// links to the tab. "View as table" lists every meeting with a link, newest first.
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChartLine, Table2 } from "lucide-react";
import { useCallback, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import type { ChartSeries } from "@/components/data/TimeSeriesChart";
import { formatAxisShare, formatCount, formatDate, formatShareChange, formatTableDate } from "@/lib/format";
import { barAt, stepBar, stripSeries, stripTableRows, type BankStrip, type RowBar, type StripTableRow } from "@/lib/policy/changestrip";
import type { Bank } from "@/lib/queries/policy";

const TimeSeriesChart = dynamic(() => import("@/components/data/TimeSeriesChart"), {
  ssr: false,
  loading: () => <div className="h-40 w-full" aria-hidden />,
});

const BANK_NAME: Record<Bank, string> = { Fed: "Fed (FOMC statements)", RBI: "RBI (MPC resolutions)" };

/**
 * The table's columns (#394): the group column and the date column's header and cell are the parts a second user
 * names; the rest is shared. Module 1's COLUMNS are this with its own words.
 */
export function changeStripColumns<T extends RowBar>(opts: { groupHeader: string; group: (r: T) => string; dateHeader: string; dateCell?: (r: T) => ReactNode }): Column<T>[] {
  return [
    { id: "bank", header: opts.groupHeader, cell: (r) => opts.group(r), sortValue: (r) => opts.group(r) },
    {
      id: "meeting",
      header: opts.dateHeader,
      cell: (r) => (
        <Link href={r.href} className="font-mono text-ink-1 underline">
          {opts.dateCell ? opts.dateCell(r) : formatTableDate(r.date)}
        </Link>
      ),
      sortValue: (r) => r.date,
    },
    { id: "added", header: "Words added (% of this statement)", align: "right", cell: (r) => formatShareChange(r.addedPct), sortValue: (r) => r.addedPct },
    { id: "removed", header: "Words removed (% of the previous)", align: "right", cell: (r) => formatShareChange(-r.removedPct), sortValue: (r) => r.removedPct },
    { id: "wordsAdded", header: "Words added", align: "right", cell: (r) => formatCount(r.wordsAdded), sortValue: (r) => r.wordsAdded },
    { id: "wordsRemoved", header: "Words removed", align: "right", cell: (r) => formatCount(r.wordsRemoved), sortValue: (r) => r.wordsRemoved },
  ];
}

const COLUMNS: Column<StripTableRow>[] = changeStripColumns<StripTableRow>({ groupHeader: "Bank", group: (r) => r.bank, dateHeader: "Meeting" });

function Key({ tone, children }: { tone: "ins" | "del"; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <i aria-hidden className={`inline-block h-2 w-3.5 ${tone === "ins" ? "bg-ins-ink" : "bg-del-ink"}`} />
      {children}
    </span>
  );
}

function Readout({ bar, linkText }: { bar: RowBar; linkText: string }) {
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-data text-ink-2">
      <span className="font-mono text-ink-3">{bar.label ?? formatDate(bar.date)}</span>
      <span>
        <b className="font-semibold text-ink-1">{formatShareChange(bar.addedPct)}</b> added
        <span className="text-ink-3"> ({formatCount(bar.wordsAdded)} of {formatCount(bar.wordsNew)} words)</span>
      </span>
      <span>
        <b className="font-semibold text-ink-1">{formatShareChange(-bar.removedPct)}</b> removed
        <span className="text-ink-3"> ({formatCount(bar.wordsRemoved)} of {formatCount(bar.wordsOld)} words)</span>
      </span>
      <Link href={bar.href} className="text-ink-1 underline">
        {linkText}
      </Link>
    </span>
  );
}

/** The words of one row (#394): module 1's are its bank's; a second user passes its own. */
export type ChangeStripRowCopy = {
  /** The row's heading, e.g. "Fed (FOMC statements)". */
  heading: string;
  /** Shown when the row has no bar at all. */
  emptyNote: ReactNode;
  /** The focusable group's aria-label. */
  groupLabel: string;
  /** The chart's aria-label. */
  chartLabel: string;
  /** The readout's link words, e.g. "Open what changed". */
  linkText: string;
  /** The note under the chart when some items have no bar. */
  noBarNote: (withoutBar: number, total: number) => ReactNode;
};

/**
 * One row of ChangeStrip as a part: heading, the chosen bar's readout, the two histograms and the keyboard way in.
 * `id` keeps the series ids apart when several rows share a page. Module 1's BankRow is this with its own words.
 */
export function ChangeStripRow({
  id,
  bars,
  withoutBar,
  total,
  scale,
  copy,
}: {
  id: string;
  bars: readonly RowBar[];
  withoutBar: number;
  total: number;
  scale: number;
  copy: ChangeStripRowCopy;
}) {
  const router = useRouter();
  const [picked, setPicked] = useState<string | null>(null);
  const range = useMemo(() => ({ min: -scale, max: scale }), [scale]);
  const strip = useMemo(() => ({ bars }), [bars]);
  const series = useMemo<ChartSeries[]>(() => {
    const s = stripSeries(strip);
    return [
      { id: `${id}-added`, kind: "histogram", tone: "ins", data: s.added },
      { id: `${id}-removed`, kind: "histogram", tone: "del", data: s.removed },
    ];
  }, [strip, id]);
  // The chosen bar, else the newest: a row is never without a reading.
  const bar = barAt(strip, picked) ?? bars.at(-1);
  const onHover = useCallback((date: string | null) => {
    if (date !== null) setPicked(date);
  }, []);
  const onSelect = useCallback(
    (date: string | null) => {
      const hit = barAt(strip, date);
      if (hit) router.push(hit.href);
    },
    [strip, router],
  );
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const next = stepBar(strip, bar?.date ?? null, e.key === "ArrowLeft" ? -1 : 1);
      if (next) setPicked(next.date);
    } else if (e.key === "Enter" && bar) {
      router.push(bar.href);
    }
  };

  return (
    <div className="px-3 pt-2.5 pb-1">
      <div className="mb-1 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h4 className="text-meta font-medium text-ink-2">{copy.heading}</h4>
        <span aria-live="polite">{bar && <Readout bar={bar} linkText={copy.linkText} />}</span>
      </div>
      {bars.length === 0 ? (
        <StateNote kind="unavailable">{copy.emptyNote}</StateNote>
      ) : (
        <div role="group" tabIndex={0} onKeyDown={onKeyDown} aria-label={copy.groupLabel} className="rounded-sm">
          <TimeSeriesChart series={series} onHover={onHover} onSelect={onSelect} axisFormat={formatAxisShare} valueRange={range} compact label={copy.chartLabel} />
        </div>
      )}
      {withoutBar > 0 && bars.length > 0 && <p className="pt-1 text-meta text-ink-3">{copy.noBarNote(withoutBar, total)}</p>}
    </div>
  );
}

function BankRow({ strip, scale }: { strip: BankStrip; scale: number }) {
  const copy = useMemo<ChangeStripRowCopy>(
    () => ({
      heading: BANK_NAME[strip.bank],
      emptyNote: <>No {strip.bank === "Fed" ? "FOMC statement" : "MPC resolution"} has a stored comparison with the one before it yet.</>,
      groupLabel: `${strip.bank}: share of each statement's words added and removed, by meeting. Left and right arrow keys choose a meeting; Enter opens it. View as table lists them all.`,
      chartLabel: `${strip.bank}: for each meeting, the share of the statement's words added (above the line) and removed (below), against the statement before it.`,
      linkText: "Open what changed",
      noBarNote: (n, total) => (
        <>
          {n} of {total} {strip.bank} meetings {n === 1 ? "has" : "have"} no bar: nothing earlier to compare with, or the comparison
          isn&apos;t stored.
        </>
      ),
    }),
    [strip.bank],
  );
  return <ChangeStripRow id={strip.bank} bars={strip.bars} withoutBar={strip.withoutBar} total={strip.meetings} scale={scale} copy={copy} />;
}

export function ChangeStrip({
  strips,
  scale,
  footer,
  refresh,
}: {
  /** One strip per bank, from buildChangeStrips; keep their identity between renders. */
  strips: BankStrip[];
  /** The ±scale both rows share (stripScale). */
  scale: number;
  footer?: ReactNode;
  refresh?: readonly string[];
}) {
  const [asTable, setAsTable] = useState(false);
  const rows = useMemo(() => stripTableRows(strips), [strips]);
  const empty = rows.length === 0;

  const toggle = (
    <button
      type="button"
      onClick={() => setAsTable((v) => !v)}
      aria-pressed={asTable}
      aria-label={asTable ? "View as chart" : "View as table"}
      title={asTable ? "View as chart" : "View as table"}
      className="grid size-7 place-items-center rounded-md text-ink-2 hover:bg-active hover:text-ink-1"
    >
      {asTable ? <ChartLine aria-hidden className="size-4" /> : <Table2 aria-hidden className="size-4" />}
    </button>
  );

  return (
    <Panel
      title="How much each statement changed"
      titleId="panel-change-strip"
      subtitle="Which statements were rewritten the most? Each bar is the share of a statement's words added (above the line) and removed (below), against the statement before it."
      actions={empty ? undefined : toggle}
      variant="flush"
      footer={footer}
      refresh={refresh}
    >
      {empty ? (
        <div className="p-3">
          <StateNote kind="unavailable">No statement has a stored comparison with the one before it yet.</StateNote>
        </div>
      ) : asTable ? (
        <DataTable
          columns={COLUMNS}
          rows={rows}
          rowKey={(r) => r.slug}
          caption="Share of each statement's words added and removed against the statement before it, every meeting of both banks, newest first"
          initialSort={{ id: "meeting", desc: true }}
          dense
          scroll
          empty={<StateNote kind="empty">No meetings to list.</StateNote>}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-2.5 text-data text-ink-2">
            <Key tone="ins">Words added</Key>
            <Key tone="del">Words removed</Key>
            <Qualifier>share of words, derived</Qualifier>
          </div>
          {strips.map((s) => (
            <BankRow key={s.bank} strip={s} scale={scale} />
          ))}
        </>
      )}
    </Panel>
  );
}
