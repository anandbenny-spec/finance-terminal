// /screener/rate-decisions: how have sectors moved on decision days? (2A-25; Spec 02A §8). Each Nifty 50 peer group's
// median excess move over the Nifty 50 on MPC and FOMC decision days, by bank and decision, with n, for one window
// (?window=d1|d5). A group's move on a decision is the equal-weighted mean of its members' excess moves (stored by
// the view); fewer than five decisions give no statistic. Descriptive only: "Moves on decision days, not a forecast."
// Not linked from /policy or the sidebar yet: 2A-26 adds both after its checks (decided by Claude, as in 2A-18). The
// route works for review. Gated until 2A-26 flips `equity.rate_sensitivity`.
import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { RateSectorTable, type SectorRow } from "@/components/company/RateDecisionTables";
import { Freshness } from "@/components/data/Freshness";
import { Gated } from "@/components/data/Gated";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel } from "@/components/data/Panel";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { Foot } from "@/components/policy/PanelFoot";
import { NOT_A_FORECAST, RATE_GATE, WINDOWS, rateSources, statRows, windowOf, type WindowKey } from "@/lib/equity/rate";
import { PREVIEW_FEEDS } from "@/lib/feeds";
import { readSectorPage, type SectorPageData } from "@/lib/queries/rate";
import { readFreshness } from "@/lib/queries/shell";
import { panelFreshness } from "@/lib/shell/freshness";

export const metadata: Metadata = {
  title: "Sectors on rate-decision days · Finance Terminal",
  description: "How each Nifty 50 peer group has moved against the index on RBI and Fed decision days. Informational only, not investment advice.",
};

const TASKS = PREVIEW_FEEDS.rateSensitivity;
const HELD = "Rate-sensitivity figures are held back until their checks pass.";

export function sectorHref(window: WindowKey): string {
  return `/screener/rate-decisions${window === "d1" ? "" : `?window=${window}`}`;
}

function WindowToggle({ window }: { window: WindowKey }) {
  return (
    <nav aria-label="Window after the decision" className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-data text-ink-2">
      <span>Move over</span>
      <div role="group" className="flex overflow-hidden rounded-md border border-line">
        {WINDOWS.map((w) => (
          <Link
            key={w.id}
            href={sectorHref(w.id)}
            aria-current={w.id === window ? "page" : undefined}
            title={w.note}
            className="border-r border-line px-2.5 py-1 text-meta text-ink-2 no-underline last:border-r-0 hover:text-ink-1 aria-[current=page]:bg-active aria-[current=page]:text-ink-1"
          >
            {w.label}
          </Link>
        ))}
      </div>
      <span>after the decision</span>
    </nav>
  );
}

/** The sector table's rows for one window: each peer group's stored statistics, with the group's members for the links. */
export function sectorRows(data: Extract<SectorPageData, { ok: true }>, window: WindowKey): SectorRow[] {
  const byKey = new Map(data.groups.map((g) => [g.key, g]));
  return statRows(data.stats, window)
    .filter((r) => r.stat.subject_kind === "peer_group")
    .map((r) => ({ ...r, group: byKey.get(r.subjectKey) ?? null }));
}

async function Sectors({ searchParams }: { searchParams: Promise<{ window?: string | string[] }> }) {
  const window = windowOf((await searchParams).window);
  const [data, fresh] = await Promise.all([readSectorPage(), readFreshness()]);
  const freshness = fresh.ok ? fresh.tasks : null;
  const now = data.readAt;
  const sources = rateSources(data.ok ? data.niftyAttribution : null);
  const pageFresh = panelFreshness(freshness, TASKS, null, now);
  const foot = <Foot sources={sources} tasks={TASKS} dataTo={null} freshness={freshness} now={now} />;

  return (
    <>
      <PageHeader
        crumbs={["Company research", { label: "Screener", href: "/screener" }, "Rate decisions"]}
        title="Sectors on rate-decision days"
        description="How have sectors moved against the Nifty 50 on MPC and FOMC decision days?"
        meta={
          <>
            <SourceLine lines={sources} />
            <Freshness freshness={pageFresh} />
          </>
        }
      />
      <WindowToggle window={window} />
      <div className="mt-6">
        <Panel
          title="Peer groups by bank and decision"
          titleId="panel-sector-decisions"
          subtitle="What is the median excess move of each peer group when the bank cuts, holds or hikes?"
          variant="flush"
          footer={foot}
        >
          {!data.ok ? (
            <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
          ) : (
            <Gated policies={data.policies} field={RATE_GATE} heldBackNote={HELD} showLabel={false}>
              {sectorRows(data, window).length === 0 ? (
                <div className="p-3">
                  <StateNote kind="unavailable">No peer group has decision-day moves published yet.</StateNote>
                </div>
              ) : (
                <>
                  <RateSectorTable rows={sectorRows(data, window)} caption="Median excess move over the Nifty 50 by peer group, bank and decision" />
                  <div className="border-t border-line px-3 py-2 text-meta text-ink-2">
                    <p className="mb-1">
                      A peer group&apos;s move on a decision is the equal-weighted mean of its members&apos; excess moves (their price move less the Nifty 50&apos;s over the same dates, excluding dividends, not adjusted for beta). Every
                      rate decision since 1 January 2019, today&apos;s Nifty 50 members only. Where there are fewer than 5 decisions, no statistic is shown.
                    </p>
                    <p>{NOT_A_FORECAST}</p>
                  </div>
                </>
              )}
            </Gated>
          )}
        </Panel>
      </div>
    </>
  );
}

export default function Page(props: PageProps<"/screener/rate-decisions">) {
  return (
    <Suspense fallback={<StateNote kind="empty">Loading the peer groups…</StateNote>}>
      <Sectors searchParams={props.searchParams} />
    </Suspense>
  );
}
