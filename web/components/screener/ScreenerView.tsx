"use client";

// ScreenerView (2A-18; Spec 02A §8, `/screener`): the filter bar and the table over the latest weekly snapshot. Filtering
// is client-side over the rows the page read (at most 200). Every figure is a stored value formatted by lib/format.ts;
// a cell with no value reads "Held" (the stored reason in its tooltip), "N/A" or a dash, never a blank; the model-implied
// columns appear only where api.field_policy shows them (fail closed: a missing row hides them) and say so when they don't.
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Delta } from "@/components/data/Delta";
import { fieldPolicy, Gated, type FieldPolicies } from "@/components/data/Gated";
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { changeOf } from "@/lib/delta";
import {
  applyFilters,
  cellState,
  columnLabel,
  isActive,
  NO_FILTERS,
  peerGroups,
  RANGE_FILTERS,
  type FilterKey,
  type Filters,
} from "@/lib/equity/screener";
import {
  formatAxisNumber,
  formatDate,
  formatFraction,
  formatFractionChange,
  formatFractionPoints,
  formatMoney,
  formatMultiple,
  formatPrice,
  formatRupeesAsCrore,
  formatShare,
  NOT_APPLICABLE,
  NOT_AVAILABLE,
  NOT_AVAILABLE_LABEL,
} from "@/lib/format";
import type { ScreenerRow } from "@/lib/queries/api";

const INPUT =
  "h-8 w-24 rounded-sm border border-line-input bg-raised px-2 text-data text-ink-1 placeholder:text-ink-3 focus-visible:outline-2 focus-visible:outline-amber";
const CHIP_ON = "bg-amber-wash text-ink-1";
const CHIP_OFF = "text-ink-2 hover:bg-active";

/** An empty cell: "Held" with its reason, "N/A" with its reason, or a dash; always in the tooltip and for screen readers. */
function Empty({ row, column }: { row: ScreenerRow; column: string }) {
  const state = cellState(row, column, null);
  if (state.kind === "missing") {
    return (
      <span title={NOT_AVAILABLE_LABEL}>
        <span aria-hidden>{NOT_AVAILABLE}</span>
        <span className="sr-only">{NOT_AVAILABLE_LABEL}</span>
      </span>
    );
  }
  if (state.kind !== "held" && state.kind !== "na") return null;
  const word = state.kind === "na" ? NOT_APPLICABLE : "Held";
  return (
    <span title={state.reason}>
      <span aria-hidden>{word}</span>
      <span className="sr-only">
        {word}: {state.reason}
      </span>
    </span>
  );
}

function money(row: ScreenerRow, value: number | null): string {
  return row.currency === "INR" ? formatRupeesAsCrore(value) : formatMoney(value, row.currency);
}

type Num = (r: ScreenerRow) => number | null;

export function ScreenerView({
  rows,
  indexes,
  policies,
  footer,
}: {
  rows: ScreenerRow[];
  indexes: { slug: string; name: string }[];
  policies: FieldPolicies;
  footer: ReactNode;
}) {
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const groups = useMemo(() => peerGroups(rows), [rows]);
  const shown = useMemo(() => applyFilters(rows, filters), [rows, filters]);
  const snapshot = rows.map((r) => r.snapshot_date).sort().at(-1) ?? null;

  const setBound = (key: FilterKey, which: "min" | "max", text: string) =>
    setFilters((f) => ({ ...f, ranges: { ...f.ranges, [key]: { min: f.ranges[key]?.min ?? "", max: f.ranges[key]?.max ?? "", [which]: text } } }));
  const toggleIndex = (slug: string) =>
    setFilters((f) => ({ ...f, indexes: f.indexes.includes(slug) ? f.indexes.filter((s) => s !== slug) : [...f.indexes, slug] }));

  const labelled = (header: string, column: string, fallback?: string | null) => {
    const label = columnLabel(rows, column) ?? fallback ?? null;
    return label ? `${header} (${label})` : header;
  };
  const growthShown = (fieldPolicy(policies, "equity.implied_growth")?.status ?? "hidden") !== "hidden";
  const roeShown = (fieldPolicy(policies, "equity.implied_roe")?.status ?? "hidden") !== "hidden";
  const peerShown = rows.some((r) => r.peer_group_label);

  const numberColumn = (
    id: string,
    header: string,
    value: Num,
    render: (v: number, r: ScreenerRow) => ReactNode,
  ): Column<ScreenerRow> => ({
    id,
    header,
    align: "right",
    sortValue: value,
    cell: (r) => {
      const v = value(r);
      return v === null ? <Empty row={r} column={id} /> : render(v, r);
    },
  });
  const gated = (field: string, node: ReactNode) => (
    <Gated policies={policies} field={field} showLabel={false}>
      {node}
    </Gated>
  );

  const columns: Column<ScreenerRow>[] = [
    {
      id: "company",
      header: "Company",
      sortValue: (r) => (r.short_name ?? r.name).toLowerCase(),
      cell: (r) => (
        <Link href={`/companies/${r.slug}`} className="text-ink-1 hover:text-amber">
          {r.short_name ?? r.name}
          <span className="ml-1.5 text-meta text-ink-3">{r.nse_symbol}</span>
        </Link>
      ),
    },
    ...(peerShown
      ? [
          {
            id: "peer_group",
            header: "Peer group",
            sortValue: (r: ScreenerRow) => r.peer_group_label?.toLowerCase() ?? null,
            cell: (r: ScreenerRow) => r.peer_group_label ?? <Empty row={r} column="peer_group" />,
          } satisfies Column<ScreenerRow>,
        ]
      : []),
    numberColumn("price", "Price", (r) => r.price, (v, r) => formatPrice(v, r.currency)),
    numberColumn("market_cap", "Market cap", (r) => r.market_cap, (v, r) => money(r, v)),
    numberColumn("pe_ttm", labelled("P/E", "pe_ttm", "trailing 12 months"), (r) => r.pe_ttm, (v, r) => formatMultiple(v, r.market)),
    numberColumn("pb", "P/B", (r) => r.pb, (v, r) => formatMultiple(v, r.market)),
    numberColumn("ev_ebit", labelled("EV/EBIT", "ev_ebit", rows[0]?.ebit_label), (r) => r.ev_ebit, (v, r) => formatMultiple(v, r.market)),
    numberColumn("ev_sales", "EV/sales", (r) => r.ev_sales, (v, r) => formatMultiple(v, r.market)),
    numberColumn("dividend_yield", "Dividend yield", (r) => r.dividend_yield, (v) => formatFraction(v)),
    numberColumn("roe", labelled("ROE", "roe"), (r) => r.roe, (v) => formatFraction(v)),
    numberColumn("ebit_margin", labelled("EBIT margin", "ebit_margin", rows[0]?.ebit_label), (r) => r.ebit_margin, (v) => formatFraction(v)),
    numberColumn("revenue_cagr_3y", labelled("Revenue CAGR, 3 years", "revenue_cagr_3y"), (r) => r.revenue_cagr_3y, (v) => formatFraction(v)),
    numberColumn("revenue_cagr_5y", labelled("Revenue CAGR, 5 years", "revenue_cagr_5y"), (r) => r.revenue_cagr_5y, (v) => formatFraction(v)),
    numberColumn("debt_to_equity", labelled("Debt/equity", "debt_to_equity"), (r) => r.debt_to_equity, (v, r) => formatMultiple(v, r.market, 2)),
    numberColumn("promoter_pct", "Promoter holding", (r) => r.promoter_pct, (v) => formatShare(v)),
    numberColumn("beta", labelled("Beta", "beta", rows[0]?.beta_label), (r) => r.beta, (v) => formatAxisNumber(v, 2)),
    numberColumn("return_1y", "1-year return (price only)", (r) => r.return_1y, (v) => <Delta change={changeOf(formatFractionChange(v))} />),
    ...(growthShown
      ? [
          numberColumn("implied_growth", labelled("Implied growth", "implied_growth", rows[0]?.implied_growth_label), (r) => r.implied_growth, (v) => gated("equity.implied_growth", formatFraction(v))),
          numberColumn("delivered_growth", "Delivered growth", (r) => r.delivered_growth, (v) => gated("equity.implied_growth", formatFraction(v))),
          numberColumn("growth_gap", "Growth gap", (r) => r.growth_gap, (v) => gated("equity.implied_growth", formatFractionPoints(v))),
        ]
      : []),
    ...(roeShown
      ? [
          numberColumn("implied_roe", labelled("Implied ROE", "implied_roe", rows[0]?.implied_roe_label), (r) => r.implied_roe, (v) => gated("equity.implied_roe", formatFraction(v))),
          numberColumn("delivered_roe", "Delivered ROE", (r) => r.delivered_roe, (v) => gated("equity.implied_roe", formatFraction(v))),
          numberColumn("roe_gap", "ROE gap", (r) => r.roe_gap, (v) => gated("equity.implied_roe", formatFractionPoints(v))),
        ]
      : []),
  ];

  const active = isActive(filters);
  return (
    <div className="flex flex-col gap-4">
      <Panel title="Filters" titleId="screener-filters" subtitle="Narrow the list; a company with no figure for a filtered column is left out.">
        <div className="flex flex-col gap-3">
          {indexes.length > 0 && (
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Index">
              <span className="text-meta text-ink-2">Index</span>
              {indexes.map((ix) => (
                <button
                  key={ix.slug}
                  type="button"
                  aria-pressed={filters.indexes.includes(ix.slug)}
                  onClick={() => toggleIndex(ix.slug)}
                  className={`rounded-sm border border-line-strong px-2.5 py-1 text-meta ${filters.indexes.includes(ix.slug) ? CHIP_ON : CHIP_OFF}`}
                >
                  {ix.name}
                </button>
              ))}
            </div>
          )}
          {groups.length > 0 && (
            <label className="flex flex-wrap items-center gap-2 text-meta text-ink-2">
              Peer group
              <select
                value={filters.peerGroup ?? ""}
                onChange={(e) => setFilters((f) => ({ ...f, peerGroup: e.target.value || null }))}
                className="h-8 rounded-sm border border-line-input bg-raised px-2 text-data text-ink-1 focus-visible:outline-2 focus-visible:outline-amber"
              >
                <option value="">All peer groups</option>
                {groups.map((g) => (
                  <option key={g.key} value={g.key}>
                    {g.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
            {RANGE_FILTERS.map((f) => (
              <fieldset key={f.key} className="flex flex-wrap items-center gap-2">
                <legend className="mb-1 text-meta text-ink-2">
                  {f.label} ({f.unit})
                </legend>
                <input
                  type="text"
                  inputMode="decimal"
                  aria-label={`${f.label}, minimum, ${f.unit}`}
                  placeholder="Min"
                  value={filters.ranges[f.key]?.min ?? ""}
                  onChange={(e) => setBound(f.key, "min", e.target.value)}
                  className={INPUT}
                />
                <span aria-hidden className="text-ink-3">
                  to
                </span>
                <input
                  type="text"
                  inputMode="decimal"
                  aria-label={`${f.label}, maximum, ${f.unit}`}
                  placeholder="Max"
                  value={filters.ranges[f.key]?.max ?? ""}
                  onChange={(e) => setBound(f.key, "max", e.target.value)}
                  className={INPUT}
                />
              </fieldset>
            ))}
          </div>
          <div>
            <button
              type="button"
              onClick={() => setFilters(NO_FILTERS)}
              disabled={!active}
              className="h-8 rounded-md border border-line-input px-3 text-ui text-ink-1 hover:bg-active disabled:opacity-50"
            >
              Clear filters
            </button>
          </div>
        </div>
      </Panel>
      <Panel
        title="Nifty 50 screener"
        titleId="screener-table"
        variant="flush"
        subtitle={
          <>
            Which Nifty 50 companies fit my filters, and what does today&apos;s price imply?{" "}
            {snapshot && <>Weekly snapshot of {formatDate(snapshot)}; </>}
            <span role="status">
              {shown.length} of {rows.length} companies
            </span>
            . Figures in each company&apos;s reporting currency.
          </>
        }
        footer={footer}
      >
        {(!growthShown || !roeShown) && (
          <p className="border-b border-line px-3 py-2 text-meta">
            <StateNote kind="held">
              {growthShown || roeShown ? "Part of the model-implied" : "The model-implied"} columns (implied growth, implied ROE) are held back until their
              checks pass.
            </StateNote>
          </p>
        )}
        <DataTable
          columns={columns}
          rows={shown}
          rowKey={(r) => r.slug}
          caption="Nifty 50 screener: one row per company, from the latest weekly snapshot"
          initialSort={{ id: "market_cap", desc: true }}
          scroll
          empty={<StateNote kind="empty">No company matches these filters.</StateNote>}
        />
      </Panel>
    </div>
  );
}
