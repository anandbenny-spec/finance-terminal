// "Key figures" (2A-19; Spec 02A §8, Overview): P/E, P/B, EV/EBIT, dividend yield, ROE, EBIT margin, net margin and both
// betas, each exactly as `api.company_metrics` stores it, with the stored label (derived, annualised, bottom-up, …) and
// its as-of date. A figure that is held back says "Held" and why; one that doesn't apply says "N/A" and why (told apart by
// the view's `state`, never by parsing the reason); a figure with no stored row says it is not available. EV figures
// don't apply to a lender or an insurer (Brief §6.3).
import type { ReactNode } from "react";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { Stat, StatRow } from "@/components/data/Stat";
import { STAT_PERIODS, isLender, pickMetric, type StatKey } from "@/lib/equity/company";
import {
  NOT_APPLICABLE,
  NOT_AVAILABLE,
  NOT_AVAILABLE_LABEL,
  formatAxisNumber,
  formatDate,
  formatFraction,
  formatMultiple,
  type Market,
} from "@/lib/format";
import type { CompanyMetricRow, CompanyRow } from "@/lib/queries/api";

type Spec = { key: StatKey; label: string; format: (value: number, market: Market) => string };

const SPECS: Spec[] = [
  { key: "pe_ttm", label: "P/E, last four quarters", format: (v, m) => formatMultiple(v, m) },
  { key: "pb", label: "P/B", format: (v, m) => formatMultiple(v, m) },
  { key: "ev_ebit_ttm", label: "EV/EBIT, last four quarters", format: (v, m) => formatMultiple(v, m) },
  { key: "dividend_yield", label: "Dividend yield", format: (v) => formatFraction(v, 2) },
  { key: "roe", label: "Return on equity", format: (v) => formatFraction(v) },
  { key: "ebit_margin", label: "EBIT margin", format: (v) => formatFraction(v) },
  { key: "net_margin", label: "Net margin", format: (v) => formatFraction(v) },
  { key: "beta_regression", label: "Beta, regression", format: (v) => formatAxisNumber(v, 2) },
  { key: "beta_bottom_up", label: "Beta, bottom-up", format: (v) => formatAxisNumber(v, 2) },
];

const NOT_FOR_LENDERS = "Not meaningful for a lender or an insurer.";

function statOf(spec: Spec, rows: readonly CompanyMetricRow[], company: CompanyRow) {
  const row = pickMetric(rows, spec.key, STAT_PERIODS[spec.key]);
  const market = company.market;
  let value: string = NOT_AVAILABLE;
  let context: ReactNode = <span title={NOT_AVAILABLE_LABEL}>{NOT_AVAILABLE_LABEL}</span>;
  let asOf: ReactNode = undefined;
  if (row) {
    asOf = `As of ${formatDate(row.period_end)}`;
    if (row.state === "value" && row.value !== null) {
      value = spec.format(row.value, market);
      context = row.label_kind ? <Qualifier>{row.label_kind}</Qualifier> : undefined;
    } else if (row.state === "not_applicable") {
      value = NOT_APPLICABLE;
      context = row.held_reason ?? NOT_FOR_LENDERS;
    } else {
      value = "Held";
      context = row.held_reason ?? "Held back until its checks pass.";
    }
  } else if (spec.key === "ev_ebit_ttm" && isLender(company.valuation_family)) {
    value = NOT_APPLICABLE;
    context = NOT_FOR_LENDERS;
  }
  return { value, context, asOf };
}

export function KeyFigures({ company, metrics, footer }: { company: CompanyRow; metrics: readonly CompanyMetricRow[]; footer: ReactNode }) {
  const stats = SPECS.map((spec) => ({ spec, ...statOf(spec, metrics, company) }));
  const fillers = (4 - (stats.length % 4)) % 4; // the grid's last row stays whole: a blank cell, not a gap in the divider colour
  return (
    <Panel
      title="Key figures"
      titleId="panel-key-figures"
      subtitle="What do the latest filings and today's price say about this company?"
      market={company.market}
      footer={footer}
      variant="flush"
    >
      <StatRow>
        {stats.map(({ spec, value, context, asOf }) => (
          <Stat key={spec.key} label={spec.label} value={value} context={context} asOf={asOf} />
        ))}
        {Array.from({ length: fillers }, (_, i) => (
          <div key={`filler-${i}`} aria-hidden className="bg-panel" />
        ))}
      </StatRow>
    </Panel>
  );
}
