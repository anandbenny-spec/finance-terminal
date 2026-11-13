// /screener: which Nifty 50 companies fit my filters? (2A-18; Spec 02A §8). One panel of filters and a sortable table over
// the latest weekly snapshot, every figure a row `api.screener` returned (lib/queries/equity.ts), formatted by lib/format.ts.
// `?view=priced-in` is "What's priced in" (2A-21; Spec 02A §5.9): two ranked tables and an explainer. That view, and the tab
// that opens it, show only where api.field_policy has a `site.priced_in` row that is shown or labelled; with no row (the
// state on shipping) the tab is absent and the URL shows the ordinary screener (fails closed).
// The market control (US-06, #405; Spec 02A.1 §7): `?market=us` shows the Dow 30 in dollars, with its own sources and
// freshness; India is the default. The control shows only when the views return a US row, which they don't while the US
// companies are unpublished (Spec 02A.1 §3), so until Anand's flip the page renders exactly as before. Never both markets in one table.
// Informational only: no verdict wording (Brief §11). Not in the sidebar yet: 2A-26 adds "Company research › Screener"
// after its checks, so the shared link doesn't advertise a page still being built; the route works for review.
import type { Metadata } from "next";
import { Suspense } from "react";
import { Freshness } from "@/components/data/Freshness";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel } from "@/components/data/Panel";
import { SourceLine } from "@/components/data/SourceLine";
import { PageLoading } from "@/components/data/PageLoading";
import { StateNote } from "@/components/data/StateNote";
import { Tabs } from "@/components/data/Tabs";
import { Foot } from "@/components/policy/PanelFoot";
import { MarketChips, marketOf, screenerHref } from "@/components/screener/MarketChips";
import { PricedInView } from "@/components/screener/PricedInView";
import { ScreenerView } from "@/components/screener/ScreenerView";
import { fieldPolicy } from "@/components/data/Gated";
import { pricedInVisible, viewOf } from "@/lib/equity/priced-in";
import { SEC_EDGAR, UNIVERSE, YAHOO } from "@/lib/equity/company";
import { FEEDS, PREVIEW_FEEDS } from "@/lib/feeds";
import { indexesIn } from "@/lib/equity/screener";
import { readPricedInPage, readScreenerPage } from "@/lib/queries/equity";
import { readFreshness } from "@/lib/queries/shell";
import { panelFreshness } from "@/lib/shell/freshness";

export const metadata: Metadata = {
  title: "Screener · Finance Terminal",
  description: "Filter the Nifty 50 by size, valuation multiples, returns, growth, leverage and promoter holding. Informational only, not investment advice.",
};

const NSE = "Source: NSE";
const DAMODARAN = "Source: Aswath Damodaran, NYU Stern";
async function Screener({ searchParams }: { searchParams: Promise<{ view?: string | string[]; market?: string | string[] }> }) {
  const query = await searchParams;
  const [data, fresh] = await Promise.all([readScreenerPage(), readFreshness()]);
  const freshness = fresh.ok ? fresh.tasks : null;
  const now = data.readAt;
  const switchShown = data.ok && pricedInVisible(data.policies);
  const view = viewOf(query.view, switchShown);
  // The US view exists only once the views return a US row (after Anand's flip); India is the default and every other value.
  const usShown = data.ok && data.rows.some((r) => r.market === "US");
  const market = marketOf(query.market, usShown);
  const us = market === "US";
  const universe = UNIVERSE[market];
  const rows = data.ok ? data.rows.filter((r) => r.market === market) : [];
  const priced = view === "priced-in" ? await readPricedInPage() : null;
  const dataTo = rows.map((r) => r.snapshot_date).sort().at(-1) ?? null;
  // The model-implied parts (the priced-in view, or an implied column that is shown) also depend on equity.valuation and
  // equity.snapshot: a failed valuation run must read "Update delayed" (#355). A plain table with those columns hidden doesn't.
  const impliedShown =
    data.ok && ["equity.implied_growth", "equity.implied_roe"].some((k) => (fieldPolicy(data.policies, k)?.status ?? "hidden") !== "hidden");
  const implied = view === "priced-in" || impliedShown;
  const feed = us ? (implied ? PREVIEW_FEEDS.usScreenerImplied : PREVIEW_FEEDS.usScreener) : implied ? FEEDS.screenerImplied : FEEDS.screener;
  const pageFresh = panelFreshness(freshness, feed, dataTo, now);
  const usesBeta = rows.some((r) => r.beta !== null);
  // Each market's own sources: NSE for India; SEC EDGAR (statements) and Yahoo Finance (prices) for the US.
  const own = us ? [SEC_EDGAR, YAHOO] : [NSE];
  const tabs = [
    { id: "screener", label: "Screener", href: screenerHref(market, "screener") },
    { id: "priced-in", label: "What's priced in", href: screenerHref(market, "priced-in") },
  ];

  return (
    <>
      <PageHeader
        crumbs={["Company research", "Screener"]}
        title="Screener"
        description={`Which ${universe} companies fit my filters, and what does today's price imply?`}
        meta={
          <>
            <SourceLine lines={own} />
            <Freshness freshness={pageFresh} />
          </>
        }
      />
      {switchShown && <Tabs label="Screener views" items={tabs} current={view} />}
      {usShown && <MarketChips current={market} view={view} />}
      <div className="mt-6">
        {view === "priced-in" && data.ok ? (
          priced?.ok ? (
            <PricedInView
              key={market}
              operating={priced.operating.filter((r) => r.market === market)}
              lenders={priced.lenders.filter((r) => r.market === market)}
              policies={data.policies}
              market={market}
              footer={<Foot sources={[...own, DAMODARAN]} tasks={feed} dataTo={dataTo} freshness={freshness} now={now} />}
            />
          ) : (
            <Panel title="What's priced in" titleId="priced-in-error" footer={<Foot sources={own} tasks={feed} dataTo={null} freshness={freshness} now={now} />}>
              <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
            </Panel>
          )
        ) : data.ok ? (
          rows.length === 0 ? (
            <Panel title={`${universe} screener`} titleId="screener-table" footer={<Foot sources={own} tasks={feed} dataTo={null} freshness={freshness} now={now} />}>
              <StateNote kind="unavailable">The first weekly snapshot hasn&apos;t been published yet.</StateNote>
            </Panel>
          ) : (
            <ScreenerView
              key={market}
              rows={rows}
              indexes={indexesIn(data.indexes, market)}
              policies={data.policies}
              market={market}
              footer={<Foot sources={[...own, usesBeta ? DAMODARAN : null]} tasks={feed} dataTo={dataTo} freshness={freshness} now={now} />}
            />
          )
        ) : (
          <Panel title={`${universe} screener`} titleId="screener-table" footer={<Foot sources={own} tasks={feed} dataTo={null} freshness={freshness} now={now} />}>
            <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
          </Panel>
        )}
      </div>
    </>
  );
}

export default function ScreenerPage(props: PageProps<"/screener">) {
  return (
    <Suspense fallback={<PageLoading>Loading the screener…</PageLoading>}>
      <Screener searchParams={props.searchParams} />
    </Suspense>
  );
}
