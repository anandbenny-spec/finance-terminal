"use client";

// PricedInView (2A-21; Spec 02A §5.9, §8, `/screener?view=priced-in`): how much growth each company's price implies, against
// what it has delivered. Two ranked tables, never mixed (their units differ): operating companies by growth gap, lenders by
// ROE gap. Below each, the companies with no figure and the reason. Every figure is a row an `api` view returned, formatted by
// lib/format.ts; the ranking is the view's `gap_rank`. A gap is a difference between two model figures, not a market move, so
// it carries no colour and no triangle. The columns say "implied", "delivered" and "gap" and nothing that reads as a verdict.
import Link from "next/link";
import type { ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Gated, type FieldPolicies } from "@/components/data/Gated";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import { splitRanked, unrankedReason } from "@/lib/equity/priced-in";
import { formatCount, formatFraction, formatFractionPoints, NOT_AVAILABLE, NOT_AVAILABLE_LABEL } from "@/lib/format";
import type { PricedInRow } from "@/lib/queries/api";

const MODEL_IMPLIED = "model-implied";

const name = (r: PricedInRow) => r.short_name ?? r.name;
const missing = (
  <span title={NOT_AVAILABLE_LABEL}>
    <span aria-hidden>{NOT_AVAILABLE}</span>
    <span className="sr-only">{NOT_AVAILABLE_LABEL}</span>
  </span>
);

function CompanyLink({ row }: { row: PricedInRow }) {
  return (
    <Link href={"/companies/" + row.slug + "?tab=valuation"} className="text-ink-1 hover:text-highlight">
      {name(row)}
      {row.nse_symbol && <span className="ml-1.5 text-meta text-ink-3">{row.nse_symbol}</span>}
    </Link>
  );
}

type Spec = {
  id: string;
  title: string;
  question: string;
  gate: string;
  impliedHeader: string;
  deliveredHeader: string;
  gapHeader: string;
  caption: string;
  noun: string;
};

function Table({ spec, rows, policies, footer }: { spec: Spec; rows: readonly PricedInRow[]; policies: FieldPolicies; footer: ReactNode }) {
  const { ranked, unranked } = splitRanked(rows);
  const label = rows.find((r) => r.label)?.label ?? MODEL_IMPLIED;
  const cell = (node: ReactNode) => (
    <Gated policies={policies} field={spec.gate} showLabel={false}>
      {node}
    </Gated>
  );
  const num = (id: string, header: string, value: (r: PricedInRow) => number | null, render: (v: number) => string): Column<PricedInRow> => ({
    id,
    header,
    align: "right",
    sortValue: value,
    cell: (r) => {
      const v = value(r);
      return v === null ? missing : cell(render(v));
    },
  });
  const columns: Column<PricedInRow>[] = [
    { id: "rank", header: "Rank", align: "right", sortValue: (r) => r.gap_rank, cell: (r) => (r.gap_rank === null ? missing : formatCount(r.gap_rank)) },
    { id: "company", header: "Company", sortValue: (r) => name(r).toLowerCase(), cell: (r) => <CompanyLink row={r} /> },
    num("implied", spec.impliedHeader, (r) => r.implied, (v) => formatFraction(v)),
    num("delivered", spec.deliveredHeader, (r) => r.delivered, (v) => formatFraction(v)),
    num("gap", spec.gapHeader, (r) => r.gap, (v) => formatFractionPoints(v)),
    num("years", "Years", (r) => r.delivered_years, (v) => formatCount(v)),
  ];
  return (
    <Panel title={spec.title} titleId={spec.id} variant="flush" qualifier={<Qualifier>{label}</Qualifier>} subtitle={spec.question} footer={footer}>
      <Gated policies={policies} field={spec.gate} heldBackNote={spec.title + " is held back until its checks pass."} showLabel={false}>
        {rows.length === 0 ? (
          <div className="p-3">
            <StateNote kind="unavailable">No {spec.noun} has a stored run yet.</StateNote>
          </div>
        ) : (
          <>
            {ranked.length === 0 ? (
              <div className="p-3">
                <StateNote kind="empty">No {spec.noun} has a figure to rank yet.</StateNote>
              </div>
            ) : (
              <DataTable columns={columns} rows={ranked} rowKey={(r) => r.slug} caption={spec.caption} scroll empty={null} />
            )}
            {unranked.length > 0 && (
              <div className="border-t border-line px-3 py-2 text-meta text-ink-2">
                <h3 className="mb-1 text-ui text-ink-1">With no figure</h3>
                <ul className="flex flex-col gap-1">
                  {unranked.map((r) => (
                    <li key={r.slug}>
                      <CompanyLink row={r} />: {unrankedReason(r)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </Gated>
    </Panel>
  );
}

const OPERATING: Spec = {
  id: "priced-in-operating",
  title: "Operating companies, by growth gap",
  question: "How much revenue growth does each company's price imply, against its delivered 5-year revenue growth?",
  gate: "equity.implied_growth",
  impliedHeader: "Implied growth (a year)",
  deliveredHeader: "Delivered 5-year revenue CAGR",
  gapHeader: "Gap (points a year)",
  caption: "Operating companies ranked by growth gap: implied growth, delivered 5-year revenue CAGR, the gap and the years of data",
  noun: "operating company",
};

const LENDERS: Spec = {
  id: "priced-in-lenders",
  title: "Lenders, by ROE gap",
  question: "What return on equity does each lender's price to book imply, against its delivered 5-year average ROE?",
  gate: "equity.implied_roe",
  impliedHeader: "Implied ROE",
  deliveredHeader: "Delivered ROE (5-FY average)",
  gapHeader: "Gap (points)",
  caption: "Lenders ranked by ROE gap: implied ROE, delivered 5-year average ROE, the gap and the years of data",
  noun: "lender",
};

export function PricedInView({
  operating,
  lenders,
  policies,
  footer,
}: {
  operating: readonly PricedInRow[];
  lenders: readonly PricedInRow[];
  policies: FieldPolicies;
  footer: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4">
      <Table spec={OPERATING} rows={operating} policies={policies} footer={footer} />
      <Table spec={LENDERS} rows={lenders} policies={policies} footer={footer} />
      <Panel title="How this is worked out" titleId="priced-in-explainer" variant="columns" footer={footer}>
        <div className="space-y-2 text-read text-ink-1">
          <p>
            These are model-implied figures, not forecasts. Each starts from a company&apos;s latest close and asks what the model would need to assume for its own value to match that
            price. The assumptions behind each row are one click away: the company&apos;s name opens its Valuation tab.
          </p>
          <p>
            Operating companies. A reverse discounted cash flow solves for the revenue growth, held for years 1 to 5, that makes the model&apos;s value per share equal the latest close. After
            year 5 growth fades in a straight line to the terminal rate, the risk-free rate in rupees, by year 10. Every other assumption is the one the Valuation tab shows: the EBIT margin and
            the sales-to-capital ratio from the last three filed years, tax moving from the company&apos;s recent rate to the marginal rate, discounting at the cost of capital, and no excess
            returns after year 10. Where faster growth would lower the model&apos;s value, because the return on capital is below the cost of capital, where the return on capital is so
            close to the cost of capital that growth earns little more than it costs, or where the price implies growth outside the range the solver searches, no figure is shown and the
            reason is listed below the table.
          </p>
          <p>
            Delivered means the filed figure: the 5-year revenue CAGR for an operating company, and the average of the last five fiscal years&apos; ROE for a lender. The gap is implied minus
            delivered, in percentage points, and ranks the list from the largest gap down. A figure that a structural break, such as a merger, makes incomparable is held back and listed with
            its reason.
          </p>
          <p>
            Lenders. For a stable lender the model&apos;s price to book is (ROE − g) ÷ (Ke − g), where g is the terminal growth rate and Ke the cost of equity. Solving for ROE gives implied ROE =
            g + P/B × (Ke − g), using the latest half-year book value to owners. It is never placed in the same table as the operating companies, because a growth rate and a return on equity are
            different measures. Insurers, and companies that include a lending or insurance business, are not applicable and are listed with their reasons.
          </p>
          <p>Informational only, not investment advice.</p>
        </div>
      </Panel>
    </div>
  );
}
