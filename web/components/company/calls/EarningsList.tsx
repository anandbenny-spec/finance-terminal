// The /earnings page's body (2B-20, #183; Spec 02B §13.7), apart from the route so a test can render it to HTML: the header
// with its source line and freshness, and one panel holding the table, or the empty or error state (StateNote), so the
// panel is never blank. Informational only; no verdict wording (Spec 02B §8).
import { EarningsTable } from "@/components/company/calls/EarningsTable";
import { Freshness } from "@/components/data/Freshness";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel } from "@/components/data/Panel";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { Foot } from "@/components/policy/PanelFoot";
import { listDataTo, listSourceLines } from "@/lib/equity/calls";
import { PREVIEW_FEEDS } from "@/lib/feeds";
import type { TaskFreshnessRow } from "@/lib/queries/api";
import type { EarningsListData } from "@/lib/queries/calls";
import { panelFreshness } from "@/lib/shell/freshness";

export const EARNINGS_DESCRIPTION = "The latest earnings call of each Nifty 50 company, with its AI summary and the figures management gave";
const TASKS = PREVIEW_FEEDS.calls;

export function EarningsList({ data, freshness }: { data: EarningsListData; freshness: TaskFreshnessRow[] | null }) {
  const rows = data.ok ? data.rows : [];
  const sources = data.ok ? listSourceLines(rows) : ["Source: NSE"];
  const dataTo = data.ok ? listDataTo(rows) : null;
  return (
    <>
      <PageHeader
        crumbs={["Company research", "Earnings calls"]}
        title="Earnings calls"
        description={EARNINGS_DESCRIPTION}
        meta={
          <>
            <SourceLine lines={sources} />
            <Freshness freshness={panelFreshness(freshness, TASKS, dataTo, data.readAt)} />
          </>
        }
      />
      <div className="mt-6">
        <Panel
          title="Earnings calls: the latest call per company"
          titleId="panel-earnings"
          subtitle="Which is each company's latest earnings call, and what is its status?"
          variant="flush"
          footer={<Foot sources={sources} tasks={TASKS} dataTo={dataTo} freshness={freshness} now={data.readAt} />}
        >
          {!data.ok ? (
            <div className="p-3">
              <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
            </div>
          ) : rows.length === 0 ? (
            <div className="p-3">
              <StateNote kind="unavailable">No companies are listed yet.</StateNote>
            </div>
          ) : (
            <EarningsTable rows={rows} />
          )}
        </Panel>
      </div>
    </>
  );
}
