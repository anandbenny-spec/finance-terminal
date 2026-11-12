// "Value chain" on the Overview tab (VC-06, #191; Spec 02B §12.7): where the company buys from, how it sells and how much of that
// runs through its own group, for the latest fiscal year, drawn as ValueChainFlow from api.value_chain and api.value_chain_products
// (0034). Gated by `equity.brsr_values` (seeded hidden): while it is hidden the panel shows the held note and nothing else, and no
// date or run is claimed for it. Every figure is the company's own filing ("As reported by the company; not verified by this site"),
// each from its own year, boundary stated. A held, absent or zero value is told in its words, never as a missing number; changes and
// restatements are words with no colour. The sub-page link is the way to the years, the group and the peers.
import Link from "next/link";
import { ValueChainFlow } from "@/components/company/value-chain/ValueChainFlow";
import { Gated, fieldPolicy, type FieldPolicies } from "@/components/data/Gated";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import { Foot } from "@/components/policy/PanelFoot";
import {
  AS_REPORTED,
  RESTATED_NOTE,
  XBRL_NOTE,
  VC_HELD,
  VC_VALUES_FIELD,
  assuranceLine,
  brsrSourceLine,
  filedDay,
  flowOf,
  httpsOnly,
  monthsNote,
  restatedNote,
  stateNotes,
  valueChainModel,
} from "@/lib/equity/valuechain";
import { FEEDS } from "@/lib/feeds";
import { formatDate } from "@/lib/format";
import type { TaskFreshnessRow } from "@/lib/queries/api";
import type { ValueChainOverviewData } from "@/lib/queries/valuechain";

const TASKS = FEEDS.valueChain;

export const valueChainHref = (slug: string): string => `/companies/${slug}/value-chain`;

export function ValueChainPanel({
  slug,
  name,
  family,
  data,
  freshness,
  now,
}: {
  slug: string;
  name: string;
  family: string | null | undefined;
  data: ValueChainOverviewData | undefined;
  freshness: TaskFreshnessRow[] | null;
  now: string;
}) {
  const policies: FieldPolicies = data?.policies ?? [];
  const policy = fieldPolicy(policies, VC_VALUES_FIELD);
  const shown = policy !== null && policy.status !== "hidden";
  const model = valueChainModel(data?.rows, shown);
  const year = model.kind === "years" ? model.years[model.years.length - 1] : null;
  const filed = filedDay(year?.filedAt);
  const footer = year ? (
    <Foot sources={[brsrSourceLine(year.sourceKey), filed ? `Filed ${formatDate(filed)}` : null]} tasks={TASKS} dataTo={filed} freshness={freshness} now={now} />
  ) : (
    // Held back, empty or failed: the source line only. No data is shown, so no date or run is claimed for it.
    <Foot sources={[brsrSourceLine(null)]} tasks={[]} dataTo={null} freshness={null} now={now} />
  );
  let body = null;
  // A released policy whose view still returns null columns: the database says hidden, so the panel does too (fails closed).
  if (model.kind === "held" && shown) body = <StateNote kind="held">{VC_HELD}</StateNote>;
  else if (model.kind === "error") body = <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>;
  else if (model.kind === "none") body = <StateNote kind="unavailable">No BRSR filing is published for this company yet.</StateNote>;
  else if (year) {
    const flow = flowOf(year, data?.products ?? [], { company: name, family });
    const notes = [...stateNotes(year, family), monthsNote(year)].filter((n): n is string => n !== null);
    const assurance = assuranceLine(year.cells);
    const list = httpsOnly(year.listUrl);
    body = (
      <div className="flex flex-col gap-3">
        <ValueChainFlow flow={flow} />
        <div className="flex flex-col gap-1 text-meta text-ink-2" data-vc-notes>
          <p>{AS_REPORTED}</p>
          <p>{XBRL_NOTE}</p>
          {year.cells.some((c) => restatedNote(c) !== null) && <p>{RESTATED_NOTE}</p>}
          {assurance && <p>{assurance}</p>}
          {notes.map((n) => (
            <p key={n}>{n}</p>
          ))}
        </div>
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-data">
          {list && (
            <a href={list} target="_blank" rel="noopener noreferrer" className="text-ink-1 underline hover:text-ink-2">
              Official source ↗
            </a>
          )}
          <Link href={valueChainHref(slug)} className="text-ink-1 underline hover:text-ink-2">
            Years, group and peers ›
          </Link>
        </p>
      </div>
    );
  }
  return (
    <Panel
      title="Value chain"
      titleId="panel-value-chain"
      subtitle="Where do the company's inputs come from, how does it reach its customers, and how much of that runs through its own group?"
      qualifier={policy?.status === "labelled" && policy.label ? <Qualifier>{policy.label}</Qualifier> : undefined}
      footer={footer}
    >
      <Gated policies={policies} field={VC_VALUES_FIELD} heldBackNote={VC_HELD} showLabel={false}>
        {body}
      </Gated>
    </Panel>
  );
}
