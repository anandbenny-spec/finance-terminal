// The company page's header (2A-19; Spec 02A §8): what this company is and where its price stands. Name, NSE symbol, ISIN
// and BSE code, the curated peer group (a Qualifier: curated), the indexes it belongs to, and the last close with its date
// and the day's change (a market move: sign and triangle, via Delta). The day's change is the two closes `api.companies`
// returns; it is held back when a split, bonus or consolidation went ex between those two sessions. Source and freshness
// sit in the page header's meta row.
import type { ReactNode } from "react";
import { Delta } from "@/components/data/Delta";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { Stat } from "@/components/data/Stat";
import { StateNote } from "@/components/data/StateNote";
import { changeOf } from "@/lib/delta";
import type { DayChange } from "@/lib/equity/company";
import { NOT_AVAILABLE, NOT_AVAILABLE_LABEL, formatDate, formatFractionChange, formatPrice } from "@/lib/format";
import type { CompanyRow } from "@/lib/queries/api";

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-meta text-ink-2">{label}</dt>
      <dd className="mt-0.5 text-data text-ink-1">{children}</dd>
    </div>
  );
}

const absent = <span title={NOT_AVAILABLE_LABEL}>{NOT_AVAILABLE}</span>;

export function CompanyHeader({ company, day, indexNames }: { company: CompanyRow; day: DayChange; indexNames: Record<string, string> }) {
  const indexes = company.index_slugs ?? [];
  return (
    <Panel label={`${company.name}: identifiers and last close`} market={company.market} className="mt-6">
      <div className="grid gap-4 md:grid-cols-3">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 md:col-span-2">
          <Item label="NSE symbol">
            <span className="font-mono">{company.nse_symbol}</span>
          </Item>
          <Item label="ISIN">{company.isin ? <span className="font-mono">{company.isin}</span> : absent}</Item>
          <Item label="BSE code">{company.bse_code ? <span className="font-mono">{company.bse_code}</span> : absent}</Item>
          <Item label="Peer group">
            {company.peer_group_label ? (
              <span>
                {company.peer_group_label} <Qualifier>{company.peer_group_label_kind ?? "curated"}</Qualifier>
              </span>
            ) : (
              absent
            )}
          </Item>
          <div className="col-span-2 min-w-0 md:col-span-2">
            <dt className="text-meta text-ink-2">Indexes</dt>
            <dd className="mt-0.5 flex flex-wrap gap-1.5 text-data text-ink-1">
              {indexes.length === 0
                ? absent
                : indexes.map((slug) => (
                    <span key={slug} className="rounded-sm border border-line-strong px-1.5 text-meta text-ink-1">
                      {indexNames[slug] ?? slug}
                    </span>
                  ))}
            </dd>
          </div>
        </dl>
        <Stat
          label="Last close"
          value={company.last_close === null ? NOT_AVAILABLE : formatPrice(company.last_close, company.currency)}
          context={
            day.kind === "value" ? (
              <span className="inline-flex items-center gap-1.5">
                <Delta change={changeOf(formatFractionChange(day.ratio, 2))} /> <span className="text-ink-2">on the day</span>
              </span>
            ) : day.kind === "held" ? (
              <StateNote kind="held">Day&apos;s change held back: a share action went ex between the two sessions.</StateNote>
            ) : undefined
          }
          asOf={company.last_close_date ? `Close on ${formatDate(company.last_close_date)}` : "No close published yet"}
        />
      </div>
    </Panel>
  );
}
