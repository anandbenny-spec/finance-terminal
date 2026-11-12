// /screener: which Nifty 50 companies fit my filters? (2A-18; Spec 02A §8). One panel of filters and a sortable table over
// the latest weekly snapshot, every figure a row `api.screener` returned (lib/queries/equity.ts), formatted by lib/format.ts.
// `?view=priced-in` is "What's priced in" (2A-21; Spec 02A §5.9): two ranked tables and an explainer. That view, and the tab
// that opens it, show only where api.field_policy has a `site.priced_in` row that is shown or labelled; with no row (the
// state on shipping) the tab is absent and the URL shows the ordinary screener (fails closed).
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
import { PricedInView } from "@/components/screener/PricedInView";
import { ScreenerView } from "@/components/screener/ScreenerView";
import { fieldPolicy } from "@/components/data/Gated";
import { pricedInVisible, viewOf } from "@/lib/equity/priced-in";
import { FEEDS } from "@/lib/feeds";
import { readPricedInPage, readScreenerPage } from "@/lib/queries/equity";
import { readFreshness } from "@/lib/queries/shell";
import { panelFreshness } from "@/lib/shell/freshness";

export const metadata: Metadata = {
  title: "Screener · Finance Terminal",
  description: "Filter the Nifty 50 by size, valuation multiples, returns, growth, leverage and promoter holding. Informational only, not investment advice.",
};

const NSE = "Source: NSE";
const DAMODARAN = "Source: Aswath Damodaran, NYU Stern";
const TABS = [
  { id: "screener", label: "Screener", href: "/screener" },
  { id: "priced-in", label: "What's priced in", href: "/screener?view=priced-in" },
] as const;

async function Screener({ searchParams }: { searchParams: Promise<{ view?: string | string[] }> }) {
  const query = await searchParams;
  const [data, fresh] = await Promise.all([readScreenerPage(), readFreshness()]);
  const freshness = fresh.ok ? fresh.tasks : null;
  const now = data.readAt;
  const switchShown = data.ok && pricedInVisible(data.policies);
  const view = viewOf(query.view, switchShown);
  const priced = view === "priced-in" ? await readPricedInPage() : null;
  const dataTo = data.ok ? (data.rows.map((r) => r.snapshot_date).sort().at(-1) ?? null) : null;
  // The model-implied parts (the priced-in view, or an implied column that is shown) also depend on equity.valuation and
  // equity.snapshot: a failed valuation run must read "Update delayed" (#355). A plain table with those columns hidden doesn't.
  const impliedShown =
    data.ok && ["equity.implied_growth", "equity.implied_roe"].some((k) => (fieldPolicy(data.policies, k)?.status ?? "hidden") !== "hidden");
  const feed = view === "priced-in" || impliedShown ? FEEDS.screenerImplied : FEEDS.screener;
  const pageFresh = panelFreshness(freshness, feed, dataTo, now);
  const usesBeta = data.ok && data.rows.some((r) => r.beta !== null);

  return (
    <>
      <PageHeader
        crumbs={["Company research", "Screener"]}
        title="Screener"
        description="Which Nifty 50 companies fit my filters, and what does today's price imply?"
        meta={
          <>
            <SourceLine lines={[NSE]} />
            <Freshness freshness={pageFresh} />
          </>
        }
      />
      {switchShown && <Tabs label="Screener views" items={TABS} current={view} />}
      <div className="mt-6">
        {view === "priced-in" && data.ok ? (
          priced?.ok ? (
            <PricedInView
              operating={priced.operating}
              lenders={priced.lenders}
              policies={data.policies}
              footer={<Foot sources={[NSE, DAMODARAN]} tasks={feed} dataTo={dataTo} freshness={freshness} now={now} />}
            />
          ) : (
            <Panel title="What's priced in" titleId="priced-in-error" footer={<Foot sources={[NSE]} tasks={feed} dataTo={null} freshness={freshness} now={now} />}>
              <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
            </Panel>
          )
        ) : data.ok ? (
          data.rows.length === 0 ? (
            <Panel title="Nifty 50 screener" titleId="screener-table" footer={<Foot sources={[NSE]} tasks={feed} dataTo={null} freshness={freshness} now={now} />}>
              <StateNote kind="unavailable">The first weekly snapshot hasn&apos;t been published yet.</StateNote>
            </Panel>
          ) : (
            <ScreenerView
              rows={data.rows}
              indexes={data.indexes}
              policies={data.policies}
              footer={<Foot sources={[NSE, usesBeta ? DAMODARAN : null]} tasks={feed} dataTo={dataTo} freshness={freshness} now={now} />}
            />
          )
        ) : (
          <Panel title="Nifty 50 screener" titleId="screener-table" footer={<Foot sources={[NSE]} tasks={feed} dataTo={null} freshness={freshness} now={now} />}>
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
