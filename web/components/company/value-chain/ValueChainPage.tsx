// The value chain sub-page /companies/[slug]/value-chain (VC-06, #191; Spec 02B §12.7): the company header, then five panels, each
// answering one question from one `api` view and gated by its own field policy (all seeded hidden, 0034): the flow by year
// (a year selector, the change from the year before in words), every field in every year's BRSR, the company's own top five
// related-party counterparties with the group companies, the segments, and the peers. A part that is held back shows its held note
// and nothing else; a part with no rows says "not available" (a company with no related-party rows is NOT a company with none:
// the filing may simply not be read). A six-month related-party sum is shown with the period it covers, never as a year. No colour,
// no ranking, no median across boundaries. Server-rendered from the reads in lib/queries/valuechain.ts.
import Link from "next/link";
import { amount } from "@/components/company/cells";
import { CompanyHeader } from "@/components/company/CompanyHeader";
import { FieldsTable, GroupTable, PeersTable, RptTable, SegmentsTable, type GroupRow, type SegmentTableRow } from "@/components/company/value-chain/ValueChainTables";
import { ValueChainFlow } from "@/components/company/value-chain/ValueChainFlow";
import { Gated, fieldPolicy, type FieldPolicies } from "@/components/data/Gated";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { Foot } from "@/components/policy/PanelFoot";
import { dayChange, sourceLineFor } from "@/lib/equity/company";
import {
  AS_REPORTED,
  RESTATED_NOTE,
  XBRL_NOTE,
  GROUP_HELD,
  PEERS_HELD,
  RPT_HELD,
  SEGMENTS_HELD,
  VC_GROUP_FIELD,
  VC_HELD,
  VC_RPT_FIELD,
  VC_SEGMENTS_FIELD,
  VC_VALUES_FIELD,
  assuranceLine,
  brsrSourceLine,
  fieldsTable,
  filedDay,
  flowOf,
  httpsOnly,
  latestRpt,
  monthsNote,
  restatedNote,
  peersModel,
  periodText,
  pickYear,
  rptPeriod,
  rptRows,
  stateNotes,
  valueChainModel,
  yearParam,
  type YearCards,
} from "@/lib/equity/valuechain";
import { FEEDS } from "@/lib/feeds";
import { formatDate, formatFiledShare, formatShare } from "@/lib/format";
import type { CompanyRow, CorporateActionRow, TaskFreshnessRow } from "@/lib/queries/api";
import type { ValueChainPageData } from "@/lib/queries/valuechain";

const TASKS = FEEDS.valueChain;
const ERROR = <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>;

const isShown = (policies: FieldPolicies, ...fields: string[]): boolean =>
  fields.every((f) => {
    const p = fieldPolicy(policies, f);
    return p !== null && p.status !== "hidden";
  });

export const yearHref = (slug: string, label: string): string => `/companies/${slug}/value-chain?year=${yearParam(label)}`;

function YearNav({ slug, years, current }: { slug: string; years: readonly YearCards[]; current: string }) {
  return (
    <nav aria-label="Fiscal year" className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-data text-ink-2">
      <span>Fiscal year</span>
      <div role="group" className="flex overflow-hidden rounded-md border border-line">
        {years.map((y) => (
          <Link
            key={y.label}
            href={yearHref(slug, y.label)}
            prefetch={false}
            aria-current={y.label === current ? "page" : undefined}
            className="border-r border-line px-2.5 py-1 text-meta text-ink-2 no-underline last:border-r-0 hover:text-ink-1 aria-[current=page]:bg-active aria-[current=page]:text-ink-1"
          >
            {y.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}

/** Held back, empty or failed: the source line only. No data is shown, so no date or run is claimed for it. */
function heldFoot(now: string, source: string = brsrSourceLine(null)) {
  return <Foot sources={[source]} tasks={[]} dataTo={null} freshness={null} now={now} />;
}

/** Where the segments come from, for the panel with no row to name an exchange: the results filings, never the BRSR (#191). */
const SEGMENTS_FROM = "From the results filings";

function FlowPanel({ slug, name, family, data, asked, policies, freshness, now }: { slug: string; name: string; family: string | null; data: ValueChainPageData; asked: string | string[] | undefined; policies: FieldPolicies; freshness: TaskFreshnessRow[] | null; now: string }) {
  const shown = isShown(policies, VC_VALUES_FIELD);
  const model = valueChainModel(data.rows, shown);
  const policy = fieldPolicy(policies, VC_VALUES_FIELD);
  let body = null;
  let footer = heldFoot(now);
  if (model.kind === "held" && shown) body = <StateNote kind="held">{VC_HELD}</StateNote>;
  else if (model.kind === "error") body = ERROR;
  else if (model.kind === "none") body = <StateNote kind="unavailable">No BRSR filing is published for this company yet.</StateNote>;
  else if (model.kind === "years") {
    const year = pickYear(model.years, asked);
    const flow = flowOf(year, data.products ?? [], { company: name, family, years: model.years });
    const notes = [...stateNotes(year, family), monthsNote(year)].filter((n): n is string => n !== null);
    const assurance = assuranceLine(year.cells);
    const list = httpsOnly(year.listUrl);
    const filed = filedDay(year.filedAt);
    footer = <Foot sources={[brsrSourceLine(year.sourceKey), filed ? `Filed ${formatDate(filed)}` : null]} tasks={TASKS} dataTo={filed} freshness={freshness} now={now} />;
    body = (
      <div className="flex flex-col gap-3">
        <YearNav slug={slug} years={model.years} current={year.label} />
        <ValueChainFlow flow={flow} />
        <div className="flex flex-col gap-1 text-meta text-ink-2">
          <p>{AS_REPORTED}</p>
          <p>{XBRL_NOTE}</p>
          {year.cells.some((c) => restatedNote(c) !== null) && <p>{RESTATED_NOTE}</p>}
          {assurance && <p>{assurance}</p>}
          {notes.map((n) => (
            <p key={n}>{n}</p>
          ))}
        </div>
        {list && (
          <p className="text-data">
            <a href={list} target="_blank" rel="noopener noreferrer" className="text-ink-1 underline hover:text-ink-2">
              Official source ↗
            </a>
          </p>
        )}
      </div>
    );
  }
  return (
    <Panel
      title="The flow, by year"
      titleId="panel-vc-flow"
      subtitle="How have the company's purchases, sales and group dealings changed from one filed year to the next?"
      qualifier={policy?.status === "labelled" && policy.label ? <Qualifier>{policy.label}</Qualifier> : undefined}
      footer={footer}
    >
      <Gated policies={policies} field={VC_VALUES_FIELD} heldBackNote={VC_HELD} showLabel={false}>
        {body}
      </Gated>
    </Panel>
  );
}

function FieldsPanel({ family, data, policies, freshness, now }: { family: string | null; data: ValueChainPageData; policies: FieldPolicies; freshness: TaskFreshnessRow[] | null; now: string }) {
  const model = valueChainModel(data.rows, isShown(policies, VC_VALUES_FIELD));
  const years = model.kind === "years" ? model.years : null;
  const latest = years?.[years.length - 1];
  const filed = filedDay(latest?.filedAt ?? null);
  const footer = latest ? (
    <Foot sources={[brsrSourceLine(latest.sourceKey), filed ? `Latest filing ${formatDate(filed)}` : null]} tasks={TASKS} dataTo={filed} freshness={freshness} now={now} />
  ) : (
    heldFoot(now)
  );
  return (
    <Panel
      title="Every field"
      titleId="panel-vc-fields"
      subtitle="What did each year's BRSR say, field by field?"
      variant="flush"
      footer={footer}
    >
      <Gated policies={policies} field={VC_VALUES_FIELD} heldBackNote={VC_HELD} showLabel={false}>
        {model.kind === "held" && (
          <div className="p-3">
            <StateNote kind="held">{VC_HELD}</StateNote>
          </div>
        )}
        {model.kind === "error" && <div className="p-3">{ERROR}</div>}
        {model.kind === "none" && (
          <div className="p-3">
            <StateNote kind="unavailable">No BRSR filing is published for this company yet.</StateNote>
          </div>
        )}
        {years && <FieldsTable model={fieldsTable(years, family)} />}
        {years && <p className="border-t border-line px-3 py-2 text-meta text-ink-2">{AS_REPORTED}. A share is shown as filed; a change is told in words on the flow above. {XBRL_NOTE} {RESTATED_NOTE}</p>}
      </Gated>
    </Panel>
  );
}

function GroupPanel({ name, currency, data, latestYear, policies, freshness, now }: { name: string; currency: CompanyRow["currency"]; data: ValueChainPageData; latestYear: string | null; policies: FieldPolicies; freshness: TaskFreshnessRow[] | null; now: string }) {
  const rptShown = isShown(policies, VC_RPT_FIELD);
  const groupShown = isShown(policies, VC_VALUES_FIELD, VC_GROUP_FIELD);
  const latest = data.rpt ? latestRpt(data.rpt) : null;
  const period = latest ? rptPeriod(latest.rows) : null;
  const basis = latest?.rows[0]?.basis;
  const money = (v: number | null) => amount(v, currency);
  const none = (what: string) => (
    <StateNote kind="unavailable">Not available: no {what} related-party rows were read from this company&apos;s results filings for the latest year.</StateNote>
  );
  const groupRows: GroupRow[] =
    data.group && latestYear
      ? data.group.filter((g) => g.fy_label === latestYear && g.name !== null).map((g) => ({ key: `${g.line_no}`, name: g.name as string, relation: g.relation ?? "Not reported", share: formatFiledShare(g.share_held_pct) }))
      : [];
  const groupFiled = data.group?.find((g) => g.fy_label === latestYear)?.filed_at ?? null;
  const rptTo = latest?.rows.map((r) => r.period_to).sort().at(-1) ?? null;
  const footer = rptShown && latest ? (
    <Foot
      sources={["From the half-yearly related-party disclosures in the results filings", sourceLineFor(latest.rows[0]?.source_keys?.[0]), groupShown && groupFiled ? brsrSourceLine(data.group?.[0]?.source_key) : null]}
      tasks={TASKS}
      dataTo={rptTo}
      freshness={freshness}
      now={now}
    />
  ) : (
    heldFoot(now)
  );
  return (
    <Panel
      title="Within the group"
      titleId="panel-vc-group"
      subtitle="Who are the largest related-party buyers and suppliers, and which companies belong to the group?"
      footer={footer}
    >
      <div className="flex flex-col gap-4">
        <Gated policies={policies} field={VC_RPT_FIELD} heldBackNote={RPT_HELD} showLabel={false}>
          {data.rpt === null ? (
            ERROR
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-data text-ink-1">
                Transactions {name} itself entered into; its subsidiaries&apos; aren&apos;t included
              </p>
              {latest && period ? (
                <p className="text-meta text-ink-2" data-vc-period>
                  {latest.label}
                  {period.fullYear ? "" : " (part of the year)"}: covers {periodText(period)}
                  {period.fullYear ? "" : ", not the whole fiscal year"}. {basis === "standalone" ? "Standalone" : "Consolidated"}, from the results filings.
                </p>
              ) : null}
              <div className="flex flex-col gap-1">
                <h4 className="text-meta font-medium text-ink-2">Top 5 sales of goods or services</h4>
                <RptTable rows={latest ? rptRows(latest.rows, "Sale of goods or services", money) : []} caption="Top five related-party counterparties for sales" empty={none("sales")} />
              </div>
              <div className="flex flex-col gap-1">
                <h4 className="text-meta font-medium text-ink-2">Top 5 purchases of goods or services</h4>
                <RptTable rows={latest ? rptRows(latest.rows, "Purchase of goods or services", money) : []} caption="Top five related-party counterparties for purchases" empty={none("purchase")} />
              </div>
            </div>
          )}
        </Gated>
        <div className="flex flex-col gap-1 border-t border-line pt-3">
          <h4 className="text-meta font-medium text-ink-2">Group companies and the share held, as the BRSR lists them</h4>
          <Gated policies={policies} field={VC_GROUP_FIELD} heldBackNote={GROUP_HELD} showLabel={false}>
            {data.group === null ? (
              ERROR
            ) : groupShown ? (
              <GroupTable rows={groupRows} empty={<StateNote kind="unavailable">Not available: no group companies were read from this company&apos;s BRSR.</StateNote>} />
            ) : (
              <StateNote kind="held">{VC_HELD}</StateNote>
            )}
          </Gated>
        </div>
      </div>
    </Panel>
  );
}

function SegmentsPanel({ data, policies, freshness, now }: { data: ValueChainPageData; policies: FieldPolicies; freshness: TaskFreshnessRow[] | null; now: string }) {
  const shown = isShown(policies, VC_SEGMENTS_FIELD);
  const rows = data.segments ?? [];
  const single = rows.some((r) => r.state === "single_segment");
  const tableRows: SegmentTableRow[] = rows
    .filter((r) => r.state === "ok" && r.description !== null)
    .map((r) => ({ key: `${r.line_no}`, segment: r.description as string, revenue: amount(r.revenue, r.currency), share: formatShare(r.share_of_total_pct) }));
  const first = rows[0];
  const footer = shown && first ? (
    <Foot sources={[SEGMENTS_FROM, sourceLineFor(first.source_key)]} tasks={TASKS} dataTo={first.period_end} freshness={freshness} now={now} />
  ) : (
    heldFoot(now, SEGMENTS_FROM)
  );
  return (
    <Panel title="Segments" titleId="panel-vc-segments" subtitle="Which businesses make up the revenue? Consolidated, the latest fiscal year." footer={footer}>
      <Gated policies={policies} field={VC_SEGMENTS_FIELD} heldBackNote={SEGMENTS_HELD} showLabel={false}>
        {data.segments === null ? (
          ERROR
        ) : single ? (
          <StateNote kind="empty">One reportable segment, as filed.</StateNote>
        ) : (
          <>
            {first && <p className="mb-2 text-meta text-ink-2">{first.fy_label}, consolidated, as filed. The share is of the reportable segments&apos; total.</p>}
            <SegmentsTable rows={tableRows} empty={<StateNote kind="unavailable">Not available: no segment revenue was read from this company&apos;s results filings.</StateNote>} />
          </>
        )}
      </Gated>
    </Panel>
  );
}

function PeersPanel({ data, group, policies, freshness, now }: { data: ValueChainPageData; group: string | null; policies: FieldPolicies; freshness: TaskFreshnessRow[] | null; now: string }) {
  const shown = isShown(policies, VC_VALUES_FIELD, "equity.peer_group");
  const model = data.peers ? peersModel(data.peers) : null;
  const footer = shown && model && model.rows.length > 0 ? (
    <Foot sources={[brsrSourceLine(null)]} tasks={TASKS} dataTo={null} freshness={freshness} now={now} />
  ) : (
    heldFoot(now)
  );
  return (
    <Panel
      title="Peers"
      titleId="panel-vc-peers"
      subtitle={`How do the other companies in ${group ?? "its peer group"} source their inputs and reach customers? Each company's latest filed year and own boundary; no ranking.`}
      variant="flush"
      footer={footer}
    >
      <Gated policies={policies} field={VC_VALUES_FIELD} heldBackNote={PEERS_HELD} showLabel={false}>
        {!shown ? (
          <div className="p-3">
            <StateNote kind="held">{PEERS_HELD}</StateNote>
          </div>
        ) : model === null ? (
          <div className="p-3">{ERROR}</div>
        ) : (
          <PeersTable model={model} />
        )}
      </Gated>
    </Panel>
  );
}

export function ValueChainPage({
  company,
  actions,
  indexNames,
  data,
  year,
  freshness,
  now,
}: {
  company: CompanyRow;
  actions: CorporateActionRow[];
  indexNames: Record<string, string>;
  data: ValueChainPageData;
  year: string | string[] | undefined;
  freshness: TaskFreshnessRow[] | null;
  now: string;
}) {
  const policies: FieldPolicies = data.policies ?? [];
  const name = company.short_name ?? company.name;
  const model = valueChainModel(data.rows, isShown(policies, VC_VALUES_FIELD));
  const latestYear = model.kind === "years" ? model.years[model.years.length - 1].label : null;
  return (
    <>
      <PageHeader
        crumbs={["Company research", { label: "Screener", href: "/screener" }, { label: name, href: `/companies/${company.slug}`, prefetch: false }, "Value chain"]}
        title={`${company.name}: value chain`}
        description="Where do the company's inputs come from, how does it reach its customers, and how much of that runs through its own group?"
        meta={<SourceLine lines={[brsrSourceLine(null)]} />}
      />
      <CompanyHeader company={company} day={dayChange(company, actions)} indexNames={indexNames} />
      <p className="mt-4 text-data">
        <Link href={`/companies/${company.slug}`} prefetch={false} className="text-ink-1 underline hover:text-ink-2">
          ‹ Back to {name}
        </Link>
      </p>
      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-12">
        <FlowPanel slug={company.slug} name={name} family={company.valuation_family} data={data} asked={year} policies={policies} freshness={freshness} now={now} />
        <FieldsPanel family={company.valuation_family} data={data} policies={policies} freshness={freshness} now={now} />
        <GroupPanel name={name} currency={company.currency} data={data} latestYear={latestYear} policies={policies} freshness={freshness} now={now} />
        <SegmentsPanel data={data} policies={policies} freshness={freshness} now={now} />
        <PeersPanel data={data} group={company.peer_group_label} policies={policies} freshness={freshness} now={now} />
      </div>
    </>
  );
}
