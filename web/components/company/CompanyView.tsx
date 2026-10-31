// CompanyView (2A-19; Spec 02A §8): the company page's header, tabs and the tab shown, from one cached read
// (lib/queries/company.ts). The tab and the basis are in the URL (?tab=financials&basis=standalone), so every view can be
// linked. Only Overview and Financials exist yet: Valuation (2A-20) and Rate decisions (2A-25) add their tabs with their
// pages. Each panel carries its source line and freshness; a figure with no value says why. Server-rendered.
import Link from "next/link";
import type { ReactNode } from "react";
import { ActionsPanel } from "@/components/company/ActionsPanel";
import { CompanyHeader } from "@/components/company/CompanyHeader";
import { KeyFigures } from "@/components/company/KeyFigures";
import { PeersPanel } from "@/components/company/PeersPanel";
import { PriceChartPanel } from "@/components/company/PriceChartPanel";
import { PromoterPanel } from "@/components/company/PromoterPanel";
import { StatementTable } from "@/components/company/StatementTable";
import { Freshness } from "@/components/data/Freshness";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel } from "@/components/data/Panel";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { Tabs } from "@/components/data/Tabs";
import { Foot } from "@/components/policy/PanelFoot";
import {
  BALANCE_LINES,
  CASH_FLOW_LINES,
  DAMODARAN,
  DEBT_NOTE,
  PERIODS,
  PNL_LINES,
  TABS,
  chooseBasis,
  dayChange,
  familyKind,
  isLender,
  periodsOf,
  pickMetric,
  priceData,
  sourceLineFor,
  splitActions,
  type Basis,
  type LineSpec,
  type TabId,
} from "@/lib/equity/company";
import { PREVIEW_FEEDS } from "@/lib/feeds";
import { formatDate } from "@/lib/format";
import type { CompanyPageData } from "@/lib/queries/company";
import type { CompanyFinancialRow, TaskFreshnessRow } from "@/lib/queries/api";
import { panelFreshness } from "@/lib/shell/freshness";

type Ok = Extract<CompanyPageData, { ok: true }>;

const TASKS = PREVIEW_FEEDS.company;

const BANK_LINES: LineSpec[] = [
  { id: "gnpa_ratio", label: "Gross NPA ratio", kind: "ratio" },
  { id: "nnpa_ratio", label: "Net NPA ratio", kind: "ratio" },
  { id: "cet1_ratio", label: "CET1 ratio", kind: "ratio" },
];

const latest = (dates: (string | null | undefined)[]): string | null => dates.filter((d): d is string => Boolean(d)).sort().at(-1) ?? null;

/** The date part of the newest filing time among the rows. */
const lastFiled = (rows: readonly CompanyFinancialRow[]): string | null => latest(rows.map((r) => r.filed_at?.slice(0, 10)));

export function companyHref(slug: string, tab: TabId, basis?: Basis): string {
  const params = new URLSearchParams();
  if (tab !== "overview") params.set("tab", tab);
  if (basis === "standalone") params.set("basis", basis);
  const query = params.toString();
  return `/companies/${slug}${query ? `?${query}` : ""}`;
}

function BasisToggle({ slug, basis, available }: { slug: string; basis: Basis; available: Basis[] }) {
  return (
    <nav aria-label="Basis of the statements" className="flex flex-wrap items-center gap-x-3 gap-y-2 text-data text-ink-2">
      <span>Statements on a</span>
      <div role="group" className="flex overflow-hidden rounded-md border border-line">
        {(["consolidated", "standalone"] as const).map((b) => (
          <Link
            key={b}
            href={companyHref(slug, "financials", b)}
            aria-current={b === basis ? "page" : undefined}
            className={`border-r border-line px-2.5 py-1 text-meta no-underline last:border-r-0 hover:text-ink-1 aria-[current=page]:bg-active aria-[current=page]:text-ink-1 ${
              available.includes(b) ? "text-ink-2" : "text-ink-3"
            }`}
          >
            {b === "consolidated" ? "Consolidated" : "Standalone"}
          </Link>
        ))}
      </div>
      <span>basis</span>
    </nav>
  );
}

function Overview({ data, freshness }: { data: Ok; freshness: TaskFreshnessRow[] | null }) {
  const { company, overview } = data;
  if (!overview) return null;
  const now = data.readAt;
  const prices = priceData(overview.prices);
  const price = sourceLineFor(company.price_source_key ?? overview.prices[0]?.source_key);
  const stats = overview.metrics;
  const usesDamodaran = pickMetric(stats, "beta_bottom_up", ["market"])?.state === "value";
  const statsTo = latest(["pe_ttm", "pb", "dividend_yield", "roe"].map((k) => pickMetric(stats, k, k === "roe" ? ["year"] : ["market"])?.period_end));
  const peerRows = overview.peers;
  const peerTo = latest(peerRows.map((p) => p.snapshot_date));
  return (
    <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-12">
      <PriceChartPanel
        points={prices.points}
        rows={prices.rows}
        heldTo={prices.heldTo}
        currency={company.currency}
        market={company.market}
        name={company.name}
        footer={<Foot sources={[price]} tasks={TASKS} dataTo={prices.to} freshness={freshness} now={now} />}
      />
      <KeyFigures
        company={company}
        metrics={stats}
        footer={<Foot sources={[sourceLineFor(company.price_source_key), usesDamodaran ? DAMODARAN : null]} tasks={TASKS} dataTo={statsTo} freshness={freshness} now={now} />}
      />
      <PeersPanel
        peers={peerRows}
        market={company.market}
        peerGroup={company.peer_group_label}
        lender={isLender(company.valuation_family)}
        footer={<Foot sources={[sourceLineFor(peerRows[0]?.source_key)]} tasks={TASKS} dataTo={peerTo} freshness={freshness} now={now} />}
      />
    </div>
  );
}

function Financials({ data, basis: asked, freshness }: { data: Ok; basis: Basis; freshness: TaskFreshnessRow[] | null }) {
  const { company, financials, actions } = data;
  if (!financials) return null;
  const now = data.readAt;
  const rows = financials.financials;
  const family = familyKind(company.valuation_family);
  const { basis, available, fellBack } = chooseBasis(rows, asked);
  const source = sourceLineFor(rows[0]?.source_key);
  const foot = (shown: readonly CompanyFinancialRow[]): ReactNode => {
    const filed = lastFiled(shown);
    return (
      <Foot
        sources={[source, filed ? `Latest filing ${formatDate(filed)}` : null]}
        tasks={TASKS}
        dataTo={latest(shown.map((r) => r.period_end))}
        freshness={freshness}
        now={now}
      />
    );
  };
  const quarterly = periodsOf(rows, basis, PERIODS.quarterly);
  const annual = periodsOf(rows, basis, PERIODS.annual);
  const balance = periodsOf(rows, basis, PERIODS.balance);
  const cash = periodsOf(rows, basis, PERIODS.cashFlow, "cfo");
  const bank = family === "lender" ? periodsOf(rows, "standalone", PERIODS.quarterly) : [];
  const { upcoming, past } = splitActions(actions, data.today);
  const actionsTo = latest(actions.map((a) => a.ex_date));
  const actionSource = sourceLineFor(actions[0]?.source_key);
  const none = "No filed statements are published for this company yet.";
  return (
    <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-12">
      <div className="col-span-1 flex flex-wrap items-center gap-3 md:col-span-12">
        <BasisToggle slug={company.slug} basis={basis} available={available} />
        {fellBack && (
          <StateNote kind="empty">
            No {asked} statements are published for this company; showing {basis}.
          </StateNote>
        )}
      </div>
      <StatementTable
        title="Quarterly results"
        titleId="panel-quarterly"
        subtitle="What did the company earn in each of the last eight quarters?"
        rows={quarterly}
        lines={PNL_LINES[family]}
        currency={company.currency}
        footer={foot(quarterly)}
        emptyText={none}
      />
      <StatementTable
        title="Annual results"
        titleId="panel-annual"
        subtitle="What did it earn in each of the last five financial years?"
        rows={annual}
        lines={PNL_LINES[family]}
        currency={company.currency}
        footer={foot(annual)}
        emptyText={none}
      />
      <StatementTable
        title="Balance sheet"
        titleId="panel-balance"
        subtitle="What does it own and owe at each half-year end?"
        rows={balance}
        lines={BALANCE_LINES[family]}
        currency={company.currency}
        footer={foot(balance)}
        note={family === "operating" ? DEBT_NOTE : undefined}
        emptyText={none}
      />
      <StatementTable
        title="Cash flow"
        titleId="panel-cash-flow"
        subtitle="How much cash did it generate, spend and return, by half-year and financial year?"
        rows={cash}
        lines={CASH_FLOW_LINES}
        currency={company.currency}
        footer={foot(cash)}
        emptyText="No cash flow statement is published for this company yet."
      />
      {family === "lender" && (
        <StatementTable
          title="Bank ratios"
          titleId="panel-bank-ratios"
          subtitle="How clean is the loan book, and how well capitalised is the bank? Standalone, as banks file them."
          rows={bank}
          lines={BANK_LINES}
          metricLines={[
            { id: "roa", label: "Return on assets", key: "roa" },
            { id: "nim_proxy", label: "Net interest margin", key: "nim_proxy" },
          ]}
          metrics={financials.metrics}
          currency={company.currency}
          footer={foot(bank)}
          note="Return on assets is annualised from the filing's quarter; the net interest margin is a proxy, annualised."
          emptyText="No standalone bank filings are published for this company yet."
        />
      )}
      <PromoterPanel
        rows={financials.promoter}
        market={company.market}
        footer={
          <Foot
            sources={[sourceLineFor(financials.promoter?.[0]?.source_key)]}
            tasks={TASKS}
            dataTo={latest((financials.promoter ?? []).map((p) => p.as_of))}
            freshness={freshness}
            now={now}
          />
        }
      />
      <ActionsPanel
        upcoming={upcoming}
        past={past}
        footer={<Foot sources={[actionSource]} tasks={TASKS} dataTo={actionsTo} freshness={freshness} now={now} />}
      />
    </div>
  );
}

export function CompanyView({
  data,
  basis,
  freshness,
}: {
  data: Ok;
  basis: Basis;
  freshness: TaskFreshnessRow[] | null;
}) {
  const { company, tab } = data;
  const day = dayChange(company, data.actions);
  const now = data.readAt;
  const pageFresh = panelFreshness(freshness, TASKS, company.last_close_date, now);
  return (
    <>
      <PageHeader
        crumbs={["Company research", { label: "Screener", href: "/screener" }, company.short_name ?? company.name]}
        title={company.name}
        description="What is this company, and where does its price stand?"
        meta={
          <>
            <SourceLine lines={[sourceLineFor(company.price_source_key)]} />
            <Freshness freshness={pageFresh} />
          </>
        }
      />
      <CompanyHeader company={company} day={day} indexNames={data.indexNames} />
      <Tabs
        label={`${company.name} sections`}
        items={TABS.map((t) => ({ id: t.id, label: t.label, href: companyHref(company.slug, t.id) }))}
        current={tab}
      />
      {tab === "overview" ? <Overview data={data} freshness={freshness} /> : <Financials data={data} basis={basis} freshness={freshness} />}
    </>
  );
}

export function CompanyError({ footer }: { footer?: ReactNode }) {
  return (
    <div className="mt-6">
      <Panel title="Company" titleId="company-error" footer={footer}>
        <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
      </Panel>
    </div>
  );
}
