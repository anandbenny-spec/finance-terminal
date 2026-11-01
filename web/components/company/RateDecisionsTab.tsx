// The Rate decisions tab (2A-25; Spec 02A §8, §5.10): how this stock has moved on MPC and FOMC decision days, in excess
// of the Nifty 50, and across rate cycles, with n shown. Descriptive only: no significance test (§10, decision 9),
// "Moves on decision days, not a forecast." under every panel, decisions in words (never colour), a stock's move as a
// `Delta`. Every figure is a stored row (lib/queries/rate.ts); every panel goes through `Gated` (hidden until 2A-26's
// checks), carries its source line and freshness, and says why when it has nothing. Server-rendered; the tables are
// client components (DataTable).
import Link from "next/link";
import type { ReactNode } from "react";
import { RateCyclesTable, RateMovesTable, RateStatsTable } from "@/components/company/RateDecisionTables";
import { Gated } from "@/components/data/Gated";
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { Foot } from "@/components/policy/PanelFoot";
import {
  NOT_A_FORECAST,
  RATE_GATE,
  WINDOWS,
  cycleRows,
  movesOf,
  rateSources,
  statRows,
  type WindowKey,
} from "@/lib/equity/rate";
import { PREVIEW_FEEDS } from "@/lib/feeds";
import type { TaskFreshnessRow } from "@/lib/queries/api";
import type { CompanyPageData } from "@/lib/queries/company";

type Ok = Extract<CompanyPageData, { ok: true }>;
const TASKS = PREVIEW_FEEDS.rateSensitivity;
const HELD = "Rate-sensitivity figures are held back until their checks pass.";

const latest = (dates: (string | null | undefined)[]): string | null => dates.filter((d): d is string => Boolean(d)).sort().at(-1) ?? null;

export function rateHref(slug: string, window: WindowKey): string {
  const params = new URLSearchParams({ tab: "rate-decisions" });
  if (window !== "d1") params.set("window", window);
  return `/companies/${slug}?${params.toString()}`;
}

function WindowToggle({ slug, window }: { slug: string; window: WindowKey }) {
  return (
    <nav aria-label="Window after the decision" className="flex flex-wrap items-center gap-x-3 gap-y-2 text-data text-ink-2">
      <span>Move over</span>
      <div role="group" className="flex overflow-hidden rounded-md border border-line">
        {WINDOWS.map((w) => (
          <Link
            key={w.id}
            href={rateHref(slug, w.id)}
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

/** The line under every rate-sensitivity panel, plus an optional method note above it. */
function Body({ note, children }: { note?: ReactNode; children: ReactNode }) {
  return (
    <>
      {children}
      <div className="border-t border-line px-3 py-2 text-meta text-ink-2">
        {note && <p className="mb-1">{note}</p>}
        <p>{NOT_A_FORECAST}</p>
      </div>
    </>
  );
}

export function RateDecisionsTab({ data, window, freshness }: { data: Ok; window: WindowKey; freshness: TaskFreshnessRow[] | null }) {
  const { company, rates } = data;
  if (!rates) return null;
  const now = data.readAt;
  const sources = rateSources(rates.niftyAttribution);
  const foot = (dataTo: string | null): ReactNode => <Foot sources={sources} tasks={TASKS} dataTo={dataTo} freshness={freshness} now={now} />;

  const stats = statRows(rates.stats, window);
  const few = stats.filter((r) => r.fewMoves);
  const listed = few.flatMap((r) => movesOf(rates.moves, r.bank, r.decision, window));
  const cycles = cycleRows(rates.cycles, rates.cycleInfo);
  const statsTo = latest(rates.moves.map((m) => m.post_date));
  const cyclesTo = latest(rates.cycles.map((c) => c.post_date));
  const windowNote = WINDOWS.find((w) => w.id === window)?.note;

  const none = (what: string): ReactNode => (
    <div className="p-3">
      <StateNote kind="unavailable">{what}</StateNote>
    </div>
  );

  return (
    <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-12">
      <div className="col-span-1 flex flex-wrap items-center gap-3 md:col-span-12">
        <WindowToggle slug={company.slug} window={window} />
      </div>

      <Panel
        title="Decision days"
        titleId="panel-decision-days"
        subtitle={`How has ${company.short_name ?? company.name} moved in excess of the Nifty 50 when the MPC and the FOMC cut, held or hiked?`}
        market={company.market}
        variant="flush"
        footer={foot(statsTo)}
      >
        <Gated policies={rates.policies} field={RATE_GATE} heldBackNote={HELD} showLabel={false}>
          {stats.length === 0 ? (
            none("No decision-day moves are published for this company yet.")
          ) : (
            <Body
              note={`Excess is the stock's price move less the Nifty 50's over the same dates, in percentage points, excluding dividends and not adjusted for beta. ${windowNote ?? ""} Every rate decision since 1 January 2019. Where there are fewer than 5 decisions, the moves are listed below instead of statistics.`}
            >
              <RateStatsTable rows={stats} caption={`${company.name}: excess move over the Nifty 50 by bank and decision, ${window === "d1" ? "one day" : "five days"} after`} />
            </Body>
          )}
        </Gated>
      </Panel>

      {listed.length > 0 && (
        <Panel
          title="Decisions listed one by one"
          titleId="panel-listed-moves"
          subtitle="Which decisions are too few to summarise, and how did the stock move on each?"
          market={company.market}
          variant="flush"
          footer={foot(statsTo)}
        >
          <Gated policies={rates.policies} field={RATE_GATE} heldBackNote={HELD} showLabel={false}>
            <Body>
              <RateMovesTable rows={listed} caption={`${company.name}: moves on decisions with fewer than 5 cases, one by one`} />
            </Body>
          </Gated>
        </Panel>
      )}

      <Panel
        title="Rate cycles"
        titleId="panel-rate-cycles"
        subtitle="How has it moved over each run of rate cuts or rises, against the Nifty 50?"
        market={company.market}
        variant="flush"
        footer={foot(cyclesTo)}
      >
        <Gated policies={rates.policies} field={RATE_GATE} heldBackNote={HELD} showLabel={false}>
          {cycles.length === 0 ? (
            none("No rate cycles are published for this company yet.")
          ) : (
            <Body note="A cycle is a run of two or more moves in one direction, from the last close before the first move to the first close after the last. An ongoing cycle runs to the latest close.">
              <RateCyclesTable rows={cycles} caption={`${company.name}: price move over each rate cycle, with the Nifty 50's and the excess`} />
            </Body>
          )}
        </Gated>
      </Panel>
    </div>
  );
}
