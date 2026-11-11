// "Segment changes" on the Financials tab (DD-02, #410; Plan 05 §1.2, D8): whether the company's latest quarterly results drop,
// merge or rename a reportable segment it reported in the four quarters before, from api.segment_changes (0038, #409). Consolidated
// results, as filed; the rule is the task's (py/src/ft/equity/segments.py), and the browser derives nothing: each flag's kind, the
// segments on each side and the filings are the view's. Gated by `equity.segment_changes` (seeded hidden): while it is hidden the
// panel shows the held note and nothing else. Words, never colour (CLAUDE.md §5: nothing here is a market move), never a motive
// or a judgment (D8). Each side links to its own filing; every date goes through lib/format.ts. Server-rendered.
import type { ReactNode } from "react";
import { Gated, fieldPolicy, type FieldPolicies } from "@/components/data/Gated";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import { Foot } from "@/components/policy/PanelFoot";
import { sourceLineFor } from "@/lib/equity/company";
import { PREVIEW_FEEDS } from "@/lib/feeds";
import { dateInZone, formatCount, formatDate } from "@/lib/format";
import type { SegmentChangeRow, TaskFreshnessRow } from "@/lib/queries/api";

export const SEGMENT_FIELD = "equity.segment_changes";
export const SEGMENT_HELD = "Segment changes held back until they pass review";
const TASKS = PREVIEW_FEEDS.segmentChanges;

type Kind = NonNullable<SegmentChangeRow["kind"]>;

/** Each kind in words: the flag's heading. */
export const KIND_TITLE: Record<Kind, string> = {
  dropped: "Dropped",
  merged: "Merged",
  renamed: "Renamed or redefined",
  multi_to_single: "Now one reportable segment",
  single_to_multi: "Now several reportable segments",
  other: "Segments changed",
};

/** Each kind's neutral sentence (Plan 05 D8; DD-01's handoff): what the filings show, never why. */
export const KIND_SENTENCE: Record<Kind, string> = {
  dropped: "Not in the latest results; reported in the previous four.",
  merged: "Reported as one segment in the latest results; reported separately in the previous four.",
  renamed: "A new name in the latest results; the earlier name was reported in the previous four.",
  multi_to_single: "One reportable segment in the latest results; several in the previous quarter.",
  single_to_multi: "Several reportable segments in the latest results; one in the previous quarter.",
  other: "The segments before are not in the latest results, though reported in the previous four; the segments after are new in the latest results.",
};

/** What a side with no segments of its own says: the kind tells which. */
const EMPTY_SIDE: Partial<Record<Kind, { before?: string; after?: string }>> = {
  dropped: { after: "Not reported" },
  multi_to_single: { after: "One reportable segment" },
  single_to_multi: { before: "One reportable segment" },
};

const KINDS = new Set<string>(Object.keys(KIND_TITLE));
const isKind = (k: unknown): k is Kind => typeof k === "string" && KINDS.has(k);

/** The panel's reading of the view's rows: held back, couldn't load, nothing published, one of the three states, or the flags. */
export type SegmentModel =
  | { kind: "held" }
  | { kind: "error" }
  | { kind: "none" }
  | { kind: "single_segment" | "none_flagged"; periodEnd: string }
  | { kind: "not_compared"; periodEnd: string; quarters: number }
  | { kind: "changed"; periodEnd: string; flags: SegmentChangeRow[] };

export function segmentModel(rows: readonly SegmentChangeRow[] | null | undefined, policies: FieldPolicies | null | undefined): SegmentModel {
  const policy = fieldPolicy(policies ?? [], SEGMENT_FIELD);
  if (!policy || policy.status === "hidden") return { kind: "held" };
  if (rows === null || rows === undefined) return { kind: "error" };
  const first = rows.find((r) => r.state !== null);
  if (!first?.state) return { kind: "none" };
  const { state, period_end: periodEnd } = first;
  if (state === "changed") {
    const flags = rows.filter((r) => r.state === "changed" && isKind(r.kind)).sort((a, b) => (a.change_no ?? 0) - (b.change_no ?? 0));
    return flags.length > 0 ? { kind: "changed", periodEnd, flags } : { kind: "error" };
  }
  if (state === "not_compared") return { kind: "not_compared", periodEnd, quarters: first.quarters_compared };
  return { kind: state, periodEnd };
}

const filedDay = (at: string | null): string | null => (at ? dateInZone(at, "Asia/Kolkata") : null);

/** One side's filing: "Quarter ended 30 Sep 2020, filed 30 Oct 2020", linked to the filing (https only, as every filing link). */
function FilingLine({ periodEnd, filedAt, url }: { periodEnd: string | null; filedAt: string | null; url: string | null }) {
  const filed = filedDay(filedAt);
  const words = `Quarter ended ${formatDate(periodEnd)}${filed ? `, filed ${formatDate(filed)}` : ""}`;
  return (
    <span className="block text-meta text-ink-3">
      {url?.startsWith("https://") ? (
        <a href={url} target="_blank" rel="noopener noreferrer" className="text-ink-2 underline hover:text-ink-1">
          {words}
        </a>
      ) : (
        words
      )}
    </span>
  );
}

function Side({ term, segments, empty, filing }: { term: string; segments: string[] | null; empty: string | undefined; filing: ReactNode }) {
  const named = (segments ?? []).filter((s) => s.trim() !== "");
  return (
    <div className="flex gap-3">
      <dt className="w-14 shrink-0 text-ink-2">{term}</dt>
      <dd className="min-w-0 break-words">
        {named.length > 0 ? (
          <ul className="flex flex-col gap-0.5 text-ink-1">
            {named.map((s, i) => (
              <li key={`${i}-${s}`}>{s}</li>
            ))}
          </ul>
        ) : (
          <span className="text-ink-2">{empty ?? "None"}</span>
        )}
        {filing}
      </dd>
    </div>
  );
}

function Flag({ row }: { row: SegmentChangeRow }) {
  const kind = row.kind as Kind;
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <h4 className="text-data font-semibold text-ink-1">{KIND_TITLE[kind]}</h4>
      <p className="mt-0.5 text-data text-ink-2">{KIND_SENTENCE[kind]}</p>
      <dl className="mt-2 flex flex-col gap-2 text-data">
        <Side
          term="Before"
          segments={row.segments_before}
          empty={EMPTY_SIDE[kind]?.before}
          filing={<FilingLine periodEnd={row.previous_period_end} filedAt={row.previous_filed_at} url={row.previous_filing_url} />}
        />
        <Side
          term="After"
          segments={row.segments_after}
          empty={EMPTY_SIDE[kind]?.after}
          filing={<FilingLine periodEnd={row.period_end} filedAt={row.latest_filed_at} url={row.latest_filing_url} />}
        />
      </dl>
    </li>
  );
}

function Body({ model }: { model: Exclude<SegmentModel, { kind: "held" }> }) {
  switch (model.kind) {
    case "error":
      return <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>;
    case "none":
      return <StateNote kind="unavailable">No segments from the consolidated results are published for this company yet.</StateNote>;
    case "single_segment":
      return <StateNote kind="empty">One reportable segment, as filed in the results for the quarter ended {formatDate(model.periodEnd)}.</StateNote>;
    case "none_flagged":
      return (
        <StateNote kind="empty">
          No segment flagged: each segment reported in the four quarters before is in the results for the quarter ended {formatDate(model.periodEnd)}.
        </StateNote>
      );
    case "not_compared":
      return (
        <StateNote kind="unavailable">
          Not compared: segments are on record for {formatCount(model.quarters)} of the 4 quarters before the quarter ended {formatDate(model.periodEnd)}, and the
          comparison needs all four.
        </StateNote>
      );
    case "changed":
      return (
        <>
          <p className="mb-3 text-meta text-ink-2">
            The results for the quarter ended {formatDate(model.periodEnd)} against the four quarters before it: consolidated, segments as filed.
          </p>
          <ol className="flex flex-col divide-y divide-line">
            {model.flags.map((row) => (
              <Flag key={row.change_no ?? 0} row={row} />
            ))}
          </ol>
        </>
      );
  }
}

export function SegmentChangesPanel({
  rows,
  policies,
  freshness,
  now,
}: {
  rows: readonly SegmentChangeRow[] | null | undefined;
  policies: FieldPolicies | null | undefined;
  freshness: TaskFreshnessRow[] | null;
  now: string;
}) {
  const model = segmentModel(rows, policies);
  const policy = fieldPolicy(policies ?? [], SEGMENT_FIELD);
  const shown = model.kind !== "held" ? (rows ?? []).filter((r) => r.state !== null) : [];
  const latestFiled = shown.map((r) => filedDay(r.latest_filed_at)).filter((d): d is string => d !== null).sort().at(-1) ?? null;
  // While held back the footer carries the source line only: no data is shown, so no date or run is claimed for it.
  const footer =
    model.kind === "held" ? (
      <Foot sources={[sourceLineFor(null)]} tasks={[]} dataTo={null} freshness={null} now={now} />
    ) : (
      <Foot
        sources={[sourceLineFor(shown[0]?.source_key), latestFiled ? `Latest filing ${formatDate(latestFiled)}` : null]}
        tasks={TASKS}
        dataTo={shown[0]?.period_end ?? null}
        freshness={freshness}
        now={now}
      />
    );
  return (
    <Panel
      title="Segment changes"
      titleId="panel-segment-changes"
      subtitle="Did the latest quarterly results drop, merge or rename a segment reported in the four quarters before?"
      qualifier={policy?.status === "labelled" && policy.label ? <Qualifier>{policy.label}</Qualifier> : undefined}
      footer={footer}
    >
      <Gated policies={policies ?? []} field={SEGMENT_FIELD} heldBackNote={SEGMENT_HELD} showLabel={false}>
        {model.kind !== "held" && <Body model={model} />}
      </Gated>
    </Panel>
  );
}
