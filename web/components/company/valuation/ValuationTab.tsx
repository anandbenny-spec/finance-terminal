// The Valuation tab (2A-20; Spec 02A §8, Brief §11): what growth today's price implies, never a verdict. It leads with the
// reverse DCF (a lender's implied return on equity), then the IMF's outlook for India, every assumption with its date and
// source, and "Set your own scenario" over the stored grid. Every model-implied part goes through Gated (hidden until
// 2A-26's checks); nothing is computed here; the latest close is shown only in the assumptions table, never beside a
// scenario value. Server-rendered; the two client pieces are the tables and the selects.
import type { ReactNode } from "react";
import { AssumptionsTable } from "@/components/company/valuation/AssumptionsTable";
import { ImfTable } from "@/components/company/valuation/ImfTable";
import { ScenarioControls } from "@/components/company/valuation/ScenarioControls";
import { Gated } from "@/components/data/Gated";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { Stat, StatRow } from "@/components/data/Stat";
import { StateNote } from "@/components/data/StateNote";
import { Foot } from "@/components/policy/PanelFoot";
import { DAMODARAN, sourceLineFor } from "@/lib/equity/company";
import { GATE, assumptionRows, gridCentre, gridOptions, imfContext, leadKind, leadOf, runOf, type Lead } from "@/lib/equity/valuation";
import { PREVIEW_FEEDS } from "@/lib/feeds";
import { NOT_AVAILABLE, formatCount, formatDate, formatFraction, formatFractionPoints } from "@/lib/format";
import { IN_10Y } from "@/lib/policy/longrates";
import type { TaskFreshnessRow } from "@/lib/queries/api";
import type { CompanyPageData } from "@/lib/queries/company";

type Ok = Extract<CompanyPageData, { ok: true }>;
const TASKS = PREVIEW_FEEDS.valuation;
const HELD = "Model-implied figures are held back until their checks pass.";

function LeadBody({ lead, lender }: { lead: Lead; lender: boolean }) {
  const explain = lender
    ? "This is the return on equity at which the bank's price to book would be justified, given its cost of equity. It describes what the price implies, not what will happen."
    : "This is the revenue growth, a year for the first five years, the model needs for its value to equal the latest close, holding every other assumption below. It describes what the price implies, not what will happen.";
  if (lead.status === "not_applicable" || lead.status === "no_solution" || lead.status === null) {
    return (
      <div className="p-3">
        <StateNote kind={lead.status === "not_applicable" ? "unavailable" : "held"}>{lead.reason ?? HELD}</StateNote>
      </div>
    );
  }
  const delivered =
    lead.delivered === null
      ? { value: "Held", context: lead.deliveredReason ?? "Held back until its checks pass." }
      : { value: formatFraction(lead.delivered), context: undefined };
  return (
    <>
      <StatRow>
        <Stat
          label={lender ? "Implied return on equity" : "Implied revenue growth a year, years 1 to 5"}
          value={lead.implied === null ? NOT_AVAILABLE : formatFraction(lead.implied)}
          context={lead.label ? <Qualifier>{lead.label}</Qualifier> : undefined}
          asOf={`Price of ${formatDate(lead.asOf)}`}
        />
        <Stat
          label={lender ? "Delivered return on equity, average" : "Delivered revenue growth, 5-year CAGR"}
          value={delivered.value}
          context={delivered.context}
        />
        <Stat label="Gap, implied less delivered" value={lead.gap === null ? NOT_AVAILABLE : formatFractionPoints(lead.gap)} unit={lead.gap === null || lender ? undefined : "a year"} />
        <Stat label="Years of history" value={lead.years === null ? NOT_AVAILABLE : formatCount(lead.years)} />
      </StatRow>
      <p className="border-t border-line px-3 py-2 text-data text-ink-2">{explain}</p>
    </>
  );
}

export function ValuationTab({ data, freshness }: { data: Ok; freshness: TaskFreshnessRow[] | null }) {
  const { company, valuation } = data;
  if (!valuation) return null;
  const now = data.readAt;
  const kind = leadKind(company.valuation_family);
  const lender = kind === "implied_roe";
  const run = runOf(valuation.runs, kind);
  const def = runOf(valuation.runs, "dcf_default");
  const lead = run ? leadOf(run) : null;
  const nse = sourceLineFor(company.price_source_key);
  const attribution = (key: string) => valuation.series?.find((s) => s.key === key)?.attribution ?? null;
  const foot = (sources: (string | null)[], dataTo: string | null): ReactNode => (
    <Foot sources={sources} tasks={TASKS} dataTo={dataTo} freshness={freshness} now={now} />
  );
  const none = (
    <div className="p-3">
      <StateNote kind="unavailable">No valuation has been published for this company yet.</StateNote>
    </div>
  );

  const imf = valuation.imf ? imfContext(valuation.imf, data.today) : null;
  const options = gridOptions(valuation.scenarios);
  const centre = gridCentre(options, def);

  return (
    <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-12">
      <Panel
        title={lender ? "What return on equity does the price imply?" : "What growth does today's price imply?"}
        titleId="panel-implied"
        subtitle={lender ? "Read through the return on equity, the way a bank is valued." : "The reverse DCF: the growth the latest close needs, against the growth delivered."}
        market={company.market}
        variant="flush"
        footer={foot([nse, DAMODARAN], run?.as_of ?? null)}
      >
        <Gated policies={valuation.policies} field={GATE[kind]} heldBackNote={HELD} showLabel={false}>
          {lead ? <LeadBody lead={lead} lender={lender} /> : none}
        </Gated>
      </Panel>

      {company.market === "IN" && (
        <Panel
          title="India's economy, IMF forecasts"
          titleId="panel-imf"
          subtitle={imf ? `What does the IMF expect for India's growth and inflation? World Economic Outlook, released ${formatDate(imf.vintage)}.` : "What does the IMF expect for India's growth and inflation?"}
          variant="flush"
          footer={foot([attribution("imf.NGDP_RPCH.IND"), attribution("imf.PCPIPCH.IND")], imf?.vintage ?? null)}
        >
          {imf && imf.years.length > 0 ? (
            <ImfTable years={imf.years} />
          ) : (
            <div className="p-3">
              <StateNote kind="unavailable">The IMF&apos;s forecasts for India aren&apos;t published here yet.</StateNote>
            </div>
          )}
        </Panel>
      )}

      <Panel
        title="Assumptions"
        titleId="panel-assumptions"
        subtitle="Every input the model used, with its date and source."
        variant="flush"
        footer={foot([nse, DAMODARAN, attribution(IN_10Y)], run?.as_of ?? null)}
      >
        <Gated policies={valuation.policies} field={GATE[kind]} heldBackNote={HELD} showLabel={false}>
          {run ? (
            <AssumptionsTable
              rows={assumptionRows(run)}
              currency={company.currency}
              market={company.market}
              caption={`${company.name}: each input to the ${lender ? "implied return on equity" : "reverse DCF"}, with its value, date and source`}
            />
          ) : (
            none
          )}
        </Gated>
      </Panel>

      <Panel
        title="Set your own scenario"
        titleId="panel-scenario"
        subtitle="Choose revenue growth, EBIT margin and the cost of capital; the value is read from the stored grid."
        variant="flush"
        footer={foot([nse, DAMODARAN], valuation.scenarios[0]?.as_of ?? null)}
      >
        <Gated policies={valuation.policies} field={GATE.dcf_default} heldBackNote={HELD} showLabel={false}>
          {centre ? (
            <ScenarioControls rows={valuation.scenarios} options={options} initial={centre} currency={company.currency} />
          ) : (
            <div className="p-3">
              <StateNote kind="unavailable">{def?.reason ?? (lender ? "A lender isn't valued with a cash-flow scenario." : "No scenario grid is stored for this company yet.")}</StateNote>
            </div>
          )}
        </Gated>
      </Panel>
    </div>
  );
}
