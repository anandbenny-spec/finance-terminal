"use client";

// Call › Tone (2B-12b, #482; Spec 02B §5.7, §8): how management's tone on this call compared with the previous call's. AI-scored: the
// mean of management's passage scores on a -2 to +2 scale with its passage count, the change in words with both figures, the opening
// remarks' and the answers' own means, up to three evidence quotes (verified citations, with their PDF pages), the history across the
// company's calls (one line, with "View as table") and the rubric and rules one click away. Words and numbers only: no colour and no
// Delta, since tone is not a market move (Design 01 §1.2; CLAUDE.md §5). While ai.call_tone is hidden the tab reads nothing for it
// and this panel says it is held back. Everything shown is a view's row or a verified citation; nothing here computes a score.
import dynamic from "next/dynamic";
import { ChartLine, Table2 } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { AIBlock } from "@/components/ai/AIBlock";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { Stat, StatRow } from "@/components/data/Stat";
import { StateNote } from "@/components/data/StateNote";
import type { ChartSeries } from "@/components/data/TimeSeriesChart";
import {
  NO_EVIDENCE,
  RUBRIC,
  RULES,
  TONE_CHECK,
  TONE_HELD,
  TONE_LABEL,
  TONE_NOT_YET,
  TONE_ROLES,
  TONE_SCALE,
  changeDetail,
  changeLine,
  heldText,
  partValue,
  toneEvidence,
  toneHistory,
  toneState,
  toneValue,
  type ToneHistory,
} from "@/lib/equity/tone";
import { formatAxisChange } from "@/lib/format";
import type { CallToneRow, EarningsCallRow, FieldPolicyRow } from "@/lib/queries/api";
import type { ToneRead } from "@/lib/queries/calls";

const TimeSeriesChart = dynamic(() => import("@/components/data/TimeSeriesChart"), {
  ssr: false,
  loading: () => <div className="h-40 w-full" aria-hidden />,
});

const axisTone = (value: number): string => formatAxisChange(value, 1);

const HISTORY_COLUMNS: Column<ToneHistory>[] = [
  { id: "quarter", header: "Quarter", cell: (r) => r.quarter, sortValue: (r) => r.callSlug },
  { id: "tone", header: "Tone", align: "right", cell: (r) => <span className="font-mono">{r.mean}</span> },
  { id: "n", header: "Passages", align: "right", cell: (r) => <span className="font-mono">{r.n}</span> },
  { id: "change", header: "Change on the previous call", cell: (r) => r.change },
];

const RUBRIC_COLUMNS: Column<(typeof RUBRIC)[number]>[] = [
  { id: "score", header: "Score", align: "right", cell: (r) => <span className="font-mono">{r.score}</span> },
  { id: "means", header: "Means", cell: (r) => r.means },
  { id: "signs", header: "Typical signs", cell: (r) => r.signs, wrapCell: true },
];

/** The history across the company's calls: a one-line chart of tone per call with "View as table", or the table alone with fewer than two points. */
function History({ rows, companyName }: { rows: readonly CallToneRow[]; companyName: string }) {
  const [asTable, setAsTable] = useState(false);
  const { points, table } = useMemo(() => toneHistory(rows), [rows]);
  const series = useMemo<ChartSeries[]>(() => [{ id: "tone", kind: "line", entity: "IN", data: points }], [points]);
  const chartable = points.length >= 2;
  const showTable = asTable || !chartable;
  return (
    <div className="border-t border-line pt-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 text-data text-ink-2">
        <span>Tone of each call {TONE_SCALE}, by quarter end</span>
        <Qualifier>{TONE_LABEL}</Qualifier>
        {chartable && (
          <button
            type="button"
            onClick={() => setAsTable((v) => !v)}
            aria-pressed={asTable}
            aria-label={asTable ? "View as chart" : "View as table"}
            title={asTable ? "View as chart" : "View as table"}
            className="ml-auto grid size-7 place-items-center rounded-md text-ink-2 hover:bg-active hover:text-ink-1"
          >
            {asTable ? <ChartLine aria-hidden className="size-4" /> : <Table2 aria-hidden className="size-4" />}
          </button>
        )}
      </div>
      {showTable ? (
        <DataTable
          columns={HISTORY_COLUMNS}
          rows={table}
          rowKey={(r) => r.callSlug}
          caption={`${companyName}: tone of each call ${TONE_SCALE}, newest first`}
          dense
          scroll
          empty={<StateNote kind="empty">No calls to list.</StateNote>}
        />
      ) : (
        <div className="px-1 pt-1">
          <TimeSeriesChart series={series} label={`${companyName}: tone of each call ${TONE_SCALE}, by quarter end`} axisFormat={axisTone} compact />
        </div>
      )}
    </div>
  );
}

/** The rubric and the rules, one click away. */
function Explainer() {
  return (
    <details className="group border-t border-line px-3 py-2">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-data font-medium text-ink-1 [&::-webkit-details-marker]:hidden">
        <span className="group-open:hidden">How tone is scored</span>
        <span className="hidden group-open:inline">Hide how tone is scored</span>
      </summary>
      <div className="mt-2.5 flex flex-col gap-3">
        <DataTable columns={RUBRIC_COLUMNS} rows={RUBRIC} rowKey={(r) => r.score} caption="The tone rubric, from strongly negative to strongly positive" dense empty={<StateNote kind="empty">No rubric.</StateNote>} />
        <ul className="list-disc space-y-1.5 pl-4.5 text-data text-ink-1">
          {RULES.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        <p className="text-data text-ink-1">{TONE_CHECK}</p>
      </div>
    </details>
  );
}

function Note({ kind, children }: { kind: "empty" | "held" | "unavailable" | "error"; children: ReactNode }) {
  return (
    <div className="p-3">
      <StateNote kind={kind}>{children}</StateNote>
    </div>
  );
}

export function TonePanel({
  tone,
  policies,
  call,
  name,
  companyName,
  now,
  foot,
}: {
  tone: ToneRead;
  policies: readonly FieldPolicyRow[];
  call: EarningsCallRow;
  name: string;
  companyName: string;
  now: string;
  foot: ReactNode;
}) {
  const rows = tone.state === "ok" ? tone.rows : [];
  const state = toneState(tone.state !== "hidden", tone.state === "error" ? "error" : "ok", call, rows);
  const policy = policies.find((p) => p.field_key === "ai.call_tone");
  const label = rows[0]?.policy_label ?? (policy?.status === "labelled" ? policy.label : null) ?? TONE_LABEL;

  let body: ReactNode;
  if (state.kind === "hidden") body = <Note kind="held">{TONE_HELD}</Note>;
  else if (state.kind === "error") body = <Note kind="error">Couldn&apos;t load this panel. Try again.</Note>;
  else if (state.kind === "roles") body = <Note kind="unavailable">{TONE_ROLES}</Note>;
  else if (state.kind === "none") body = <Note kind="empty">{TONE_NOT_YET}</Note>;
  else {
    const row = state.row;
    const okTone = tone.state === "ok" ? tone : null;
    const value = toneValue(row);
    const line = changeLine(row, rows);
    const detail = changeDetail(row);
    const opening = partValue(row.opening_mean, row.opening_n);
    const answers = partValue(row.answers_mean, row.answers_n);
    const evidence = toneEvidence(okTone?.output ?? null, okTone?.citations ?? [], okTone?.pages ?? []);
    body = (
      <>
        <div className="flex flex-col gap-4 p-3">
          {state.kind === "held" || value === null ? (
            <StateNote kind="held">{heldText(row) ?? TONE_NOT_YET}</StateNote>
          ) : (
            <>
              <StatRow>
                <Stat label={`Tone on the ${call.fiscal_label} call`} value={value} unit={TONE_SCALE} context={row.n === null ? undefined : `${row.n} ${row.n === 1 ? "passage" : "passages"}`} />
                {opening && <Stat label="Opening remarks" value={opening.value} context={opening.context} />}
                {answers && <Stat label="Answers" value={answers.value} context={answers.context} />}
              </StatRow>
              {line && (
                <div>
                  <p className="text-data text-ink-1">{line}</p>
                  {detail && <p className="mt-0.5 text-meta text-ink-3">{detail}</p>}
                </div>
              )}
              {evidence.items.length > 0 && (
                <AIBlock
                  block={{
                    label: "Passages that carried the score",
                    model: okTone?.output?.model ?? "",
                    createdAt: okTone?.output?.created_at ?? now,
                    bullets: evidence.items.map((e) => ({ text: e.text, citations: [e.citation] })),
                  }}
                  now={now}
                  dense={false}
                  note={`${label}. The quote is the speaker's words.`}
                  quotes
                />
              )}
              {evidence.items.length === 0 && <StateNote kind="empty">{evidence.fallback ? NO_EVIDENCE : "Evidence quotes aren't available for this call."}</StateNote>}
            </>
          )}
        </div>
        <History rows={rows} companyName={companyName} />
        <Explainer />
      </>
    );
  }

  return (
    <Panel
      title="Tone"
      titleId="panel-call-tone"
      subtitle={`How did management's tone on this call compare with last quarter's? (${name})`}
      qualifier={state.kind === "hidden" ? undefined : <Qualifier>{label}</Qualifier>}
      variant="flush"
      footer={foot}
    >
      {body}
    </Panel>
  );
}
