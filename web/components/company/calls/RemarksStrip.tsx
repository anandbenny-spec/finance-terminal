"use client";

// The company's ChangeStrip row in the Opening remarks panel (2B-19f, #395; Spec 02B §5.13, §8): one bar per call, the share of
// the opening's words added (above the line) and removed (below) against the previous quarter's, from the view's own counts. It
// uses ChangeStrip's row and table parts (2B-19e, #394) with this panel's words. Counts and shares only: no transcript text.
// Words and position, never market colour; a share is derived, and the panel says so.
import { ChartLine, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { ChangeStripRow, Key, changeStripColumns, type ChangeStripRowCopy } from "@/components/ai/ChangeStrip";
import { DataTable } from "@/components/data/DataTable";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import type { RemarksBar } from "@/lib/equity/remarks";
import { stripScale } from "@/lib/policy/changestrip";

const COPY: ChangeStripRowCopy = {
  heading: "Opening remarks, call by call",
  emptyNote: <>No call has a stored comparison of its opening remarks with the previous quarter&apos;s yet.</>,
  groupLabel: "Share of each call's opening remarks added and removed, against the previous quarter's. Left and right arrow keys choose a call; Enter opens it. View as table lists them all.",
  chartLabel: "For each call, the share of the opening remarks' words added (above the line) and removed (below), against the previous quarter's.",
  linkText: "Open this call",
  noBarNote: (n, total) => (
    <>
      {n} of {total} {total === 1 ? "call" : "calls"} {n === 1 ? "has" : "have"} no bar: the comparison&apos;s counts can&apos;t be a share, or it has no period end.
    </>
  ),
};

const COLUMNS = changeStripColumns<RemarksBar>({
  groupHeader: "Compared with",
  group: (r) => r.baseLabel,
  dateHeader: "Call",
  dateCell: (r) => r.fiscalLabel,
  addedHeader: "Words added (% of this opening)",
  removedHeader: "Words removed (% of the previous opening)",
});

export function RemarksStrip({ bars, withoutBar, total, companyName }: { bars: RemarksBar[]; withoutBar: number; total: number; companyName: string }) {
  const [asTable, setAsTable] = useState(false);
  const scale = useMemo(() => stripScale([{ bars }]), [bars]);
  const rows = useMemo(() => [...bars].sort((a, b) => b.date.localeCompare(a.date)), [bars]);

  if (bars.length === 0) {
    return <StateNote kind="unavailable">{COPY.emptyNote}</StateNote>;
  }
  return (
    <div className="border-t border-line pt-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 text-data text-ink-2">
        <Key tone="ins">Words added</Key>
        <Key tone="del">Words removed</Key>
        <Qualifier>share of words, derived</Qualifier>
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
      </div>
      {asTable ? (
        <DataTable
          columns={COLUMNS}
          rows={rows}
          rowKey={(r) => r.callSlug}
          caption={`${companyName}: share of each call's opening remarks added and removed against the previous quarter's, newest first`}
          initialSort={{ id: "meeting", desc: true }}
          dense
          scroll
          empty={<StateNote kind="empty">No calls to list.</StateNote>}
        />
      ) : (
        <ChangeStripRow id="remarks" bars={bars} withoutBar={withoutBar} total={total} scale={scale} copy={COPY} />
      )}
    </div>
  );
}
