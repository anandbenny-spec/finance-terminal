// The Earnings calls tab (2B-19, #182; Spec 02B §8, §13.3, §13.7; 02B.1 C6): the company's calls, and for the selected call the AI
// summary, the guidance given, the figures management stated and what speakers of unread role said. Only `api` views
// (lib/queries/calls.ts); every figure and quote a stored row or a verified citation; nothing shows a transcript's text; the
// AI parts are marked (AIBlock, "AI-extracted") and gated (`Gated`). The results-context panel is cut (Plan 04 §6.3, W3 cut
// item 2): the YoY changes it needs aren't a view's columns, and the browser doesn't derive figures. Words never used on this
// page: tests/banned-words.ts. The pages are India only, so no market edge. Server-rendered; the tables are client components.
import type { ReactNode } from "react";
import { AIBlock } from "@/components/ai/AIBlock";
import { KeyChanges } from "@/components/ai/KeyChanges";
import { CallsTable, FiguresTable, GuidanceTable, callHref } from "@/components/company/calls/CallTables";
import { TabError } from "@/components/company/TabError";
import { Gated, fieldPolicy } from "@/components/data/Gated";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { Stat } from "@/components/data/Stat";
import { StateNote } from "@/components/data/StateNote";
import { Foot } from "@/components/policy/PanelFoot";
import {
  AI_EXTRACTED,
  AS_STATED,
  NO_TRANSCRIPT,
  SCANNED,
  SUMMARY_HELD,
  SUMMARY_LABEL,
  SUMMARY_NOT_YET,
  UNKNOWN_ROLE_HEADING,
  WEBSITE,
  callSourceLines,
  figureSets,
  newestFirst,
  partialLine,
  summaryLists,
} from "@/lib/equity/calls";
import { REMARKS_FIELD, REMARKS_HELD, newSpeakerNote, remarksKeyChanges, similarityText, wordsChangedText } from "@/lib/equity/remarks";
import { GUIDANCE_HELD, guidanceGroups, hitRate, unattributedLine } from "@/lib/equity/guidance";
import { PREVIEW_FEEDS } from "@/lib/feeds";
import { dateInZone, formatDate } from "@/lib/format";
import type { EarningsCallRow, RemarksChangeRow, TaskFreshnessRow } from "@/lib/queries/api";
import type { CompanyPageData } from "@/lib/queries/company";

type Ok = Extract<CompanyPageData, { ok: true }>;
const TASKS = PREVIEW_FEEDS.calls;

const SUMMARY_FIELD = "ai.call_summary";
const FIGURES_FIELD = "ai.guidance_extraction";
const FIGURES_HELD = "AI-extracted figures held back until they pass review";

const filedDay = (c: EarningsCallRow | null): string | null => (c?.filed_at ? dateInZone(c.filed_at, "Asia/Kolkata") : null);

function Note({ kind = "empty", children }: { kind?: "empty" | "held" | "unavailable" | "error"; children: ReactNode }) {
  return (
    <div className="p-3">
      <StateNote kind={kind}>{children}</StateNote>
    </div>
  );
}

/** The selected call's summary, or the one line saying why there is none. */
function Summary({ data, call, now }: { data: NonNullable<Ok["calls"]>; call: EarningsCallRow; now: string }) {
  if (call.status === "on_company_website") return <StateNote kind="unavailable">{WEBSITE}. It isn&apos;t analysed here.</StateNote>;
  if (call.status === "unreadable") return <StateNote kind="unavailable">{SCANNED}.</StateNote>;
  if (call.status !== "stored") return <StateNote kind="unavailable">This transcript isn&apos;t analysed here.</StateNote>;
  const policy = fieldPolicy(data.policies, SUMMARY_FIELD);
  if (!policy || policy.status === "hidden" || call.summary_state === "held_back") return <StateNote kind="held">{SUMMARY_HELD}</StateNote>;
  const lists = data.summary ? summaryLists(data.summary, data.citations, data.pages) : [];
  if (call.summary_state !== "available" || !data.summary || lists.length === 0) return <StateNote kind="empty">{SUMMARY_NOT_YET}</StateNote>;
  const output = data.summary;
  return (
    <Gated policies={data.policies} field={SUMMARY_FIELD} showLabel={false}>
      <div className="flex flex-col gap-4">
        {lists.map((l) => (
          <AIBlock
            key={l.heading}
            block={{ label: l.heading, model: output.model, createdAt: output.created_at, bullets: l.bullets }}
            now={now}
            dense={false}
            note={l.key === "unknown" ? `Not attributed to management. ${SUMMARY_LABEL}` : SUMMARY_LABEL}
            quotes
          />
        ))}
      </div>
    </Gated>
  );
}

const GUIDANCE_OUTCOME_FIELD = "equity.guidance_outcome";

/** Company › Guidance record (2B-19b, #267; Spec 02B §8): did the company deliver the measurable targets management gave? Words, never colour. */
function GuidanceRecord({ data, name, companyName, foot }: { data: NonNullable<Ok["calls"]>; name: string; companyName: string; foot: ReactNode }) {
  const g = data.guidance;
  const open = (f: string) => {
    const p = fieldPolicy(data.policies, f);
    return p !== null && p.status !== "hidden";
  };
  const released = g !== null && open(FIGURES_FIELD) && open(GUIDANCE_OUTCOME_FIELD);
  const outcomePolicy = fieldPolicy(data.policies, GUIDANCE_OUTCOME_FIELD);
  // g is null when the record's own reads failed (#328): the panel says so and the rest of the tab renders.
  const groups = g ? guidanceGroups(g.rows, g.citations, g.pages) : { scored: [], other: [] };
  const rate = hitRate(g?.rate ?? null);
  const line = unattributedLine(g?.unattributed ?? null);
  return (
    <Panel
      title="Guidance record"
      titleId="panel-guidance-record"
      subtitle={`When ${name}'s management gave measurable targets, did the company deliver them?`}
      qualifier={released ? <Qualifier>{outcomePolicy?.status === "labelled" && outcomePolicy.label ? `${AI_EXTRACTED}, ${outcomePolicy.label}` : AI_EXTRACTED}</Qualifier> : undefined}
      variant="flush"
      footer={foot}
    >
      {!g ? (
        <Note kind="error">Couldn&apos;t load this panel. Try again.</Note>
      ) : !released ? (
        <Note kind="held">{GUIDANCE_HELD}</Note>
      ) : (
        <>
          <div className="p-3">
            {rate.kind === "rate" ? (
              <Stat label="Delivered of scored targets" value={rate.value} context={rate.context} asOf={rate.asOf} />
            ) : (
              <StateNote kind="empty">{rate.note}</StateNote>
            )}
            {line && <p className="mt-3 text-data text-ink-2">{line}</p>}
          </div>
          {groups.scored.length > 0 && <GuidanceTable rows={groups.scored} caption={`${companyName}: guidance targets scored as delivered or missed`} />}
          {groups.other.length > 0 && <GuidanceTable rows={groups.other} caption={`${companyName}: guidance targets pending or not scored`} />}
          {groups.scored.length === 0 && groups.other.length === 0 && <Note>No guidance targets are published for this company yet.</Note>}
        </>
      )}
    </Panel>
  );
}

/** Call › Opening remarks (2B-19d, #388; Spec 02B §5.13, §8): how much of management's opening wording changed against the previous quarter's. Derived, not AI; words, never colour. */
function OpeningRemarks({
  data,
  call,
  companySlug,
  name,
  foot,
}: {
  data: NonNullable<Ok["calls"]>;
  call: EarningsCallRow;
  companySlug: string;
  name: string;
  foot: ReactNode;
}) {
  const policy = fieldPolicy(data.policies, REMARKS_FIELD);
  const released = policy !== null && policy.status !== "hidden";
  const row: RemarksChangeRow | null = data.remarks?.find((r) => r.call_slug === call.call_slug) ?? null;
  const speakers = row ? newSpeakerNote(row) : null;
  return (
    <Panel
      title="Opening remarks"
      titleId="panel-call-remarks"
      subtitle={`How much did the wording of ${name}'s opening remarks change from the previous quarter's call?`}
      qualifier={released && policy?.status === "labelled" && policy.label ? <Qualifier>{policy.label}</Qualifier> : undefined}
      footer={foot}
    >
      {!released ? (
        <StateNote kind="held">{REMARKS_HELD}</StateNote>
      ) : !data.remarks ? (
        <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
      ) : !row ? (
        <StateNote kind="empty">No comparison with an earlier call&apos;s opening remarks is published for this call.</StateNote>
      ) : (
        <div className="flex flex-col gap-4">
          <Stat
            label="Wording unchanged"
            value={similarityText(row)}
            context={`${wordsChangedText(row)}, against the ${row.base_fiscal_label} opening remarks`}
            asOf="Numbers in the text are masked before comparing."
          />
          {speakers && <p className="text-data text-ink-1">{speakers}</p>}
          <KeyChanges
            model={remarksKeyChanges(row.pairs)}
            before={{ href: callHref(companySlug, row.base_call_slug), date: row.base_fiscal_label }}
            now={{ href: callHref(companySlug, row.call_slug), date: row.fiscal_label }}
            limit={5}
          />
        </div>
      )}
    </Panel>
  );
}

export function CallsTab({ data, freshness }: { data: Ok; freshness: TaskFreshnessRow[] | null }) {
  const { company, calls } = data;
  if (!calls) return <TabError title="Earnings calls" titleId="calls-error" />;
  const now = data.readAt;
  const rows = newestFirst(calls.calls);
  const call = calls.selected;
  const foot = (c: EarningsCallRow | null): ReactNode => (
    <Foot sources={callSourceLines(c)} tasks={TASKS} dataTo={filedDay(c)} freshness={freshness} now={now} />
  );

  if (!call) {
    const w = calls.latest;
    return (
      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-12">
        <Panel title="Earnings calls" titleId="panel-calls" subtitle="Which earnings calls are published for this company?" footer={foot(null)}>
          <StateNote kind="empty">
            {NO_TRANSCRIPT}
            {w?.window_from && w.window_to ? ` between ${formatDate(w.window_from)} and ${formatDate(w.window_to)}` : ""}.
          </StateNote>
        </Panel>
      </div>
    );
  }

  const stored = call.status === "stored";
  const sets = figureSets(calls.figures, calls.citations, calls.pages);
  const partial = call.roles_partial === true;
  const figuresReady = stored && call.figures_output_id !== null;
  const policyOpen = (() => {
    const p = fieldPolicy(calls.policies, FIGURES_FIELD);
    return p !== null && p.status !== "hidden";
  })();
  const tableOf = (rowsOf: typeof sets.targets, caption: string, figureHeader: string, empty: string): ReactNode =>
    !figuresReady ? (
      <Note>Figures not generated yet.</Note>
    ) : (
      <FiguresTable rows={rowsOf} caption={caption} figureHeader={figureHeader} empty={empty} />
    );
  const name = company.short_name ?? company.name;
  const remarksReleased = (fieldPolicy(calls.policies, REMARKS_FIELD)?.status ?? "hidden") !== "hidden";

  return (
    <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-12">
      <Panel
        title="Earnings calls"
        titleId="panel-calls"
        subtitle={`Which of ${name}'s earnings calls are published, and what is each one's status?`}
        variant="flush"
        footer={foot(rows[0] ?? null)}
      >
        <CallsTable
          rows={rows}
          companySlug={company.slug}
          selected={call.call_slug}
          caption={`${company.name}: published earnings calls, newest first`}
          remarks={remarksReleased && calls.remarks ? new Map(calls.remarks.map((r) => [r.call_slug, r])) : undefined}
        />
      </Panel>

      <Panel
        title={`${call.fiscal_label} call: what was said`}
        titleId="panel-call-summary"
        subtitle="What did management say, and what did analysts ask about?"
        footer={foot(call)}
      >
        {partial && <p className="mb-3 text-data text-ink-2">{partialLine(call.unknown_role_share)}</p>}
        <Summary data={calls} call={call} now={now} />
      </Panel>

      {stored && (
        <>
          <OpeningRemarks data={calls} call={call} companySlug={company.slug} name={name} foot={foot(call)} />

          <Panel
            title="Guidance given on this call"
            titleId="panel-call-guidance"
            subtitle="Which measurable figures did management say to expect?"
            qualifier={<Qualifier>{AI_EXTRACTED}</Qualifier>}
            variant="flush"
            footer={foot(call)}
          >
            <Gated policies={calls.policies} field={FIGURES_FIELD} heldBackNote={FIGURES_HELD} showLabel={false}>
              {tableOf(
                sets.targets,
                `${company.name}: guidance given on the ${call.fiscal_label} call`,
                "Figure as stated",
                partial ? "No measurable targets found in the turns attributed to management" : "No measurable targets found on this call",
              )}
            </Gated>
          </Panel>

          <Panel
            title="Figures management stated"
            titleId="panel-call-stated"
            subtitle="Which figures did management give about results already reported?"
            qualifier={<Qualifier>{AI_EXTRACTED}</Qualifier>}
            variant="flush"
            footer={foot(call)}
          >
            <Gated policies={calls.policies} field={FIGURES_FIELD} heldBackNote={FIGURES_HELD} showLabel={false}>
              {tableOf(
                sets.stated,
                `${company.name}: figures management stated on the ${call.fiscal_label} call`,
                "Figure",
                partial ? "No figures found in the turns attributed to management" : "No figures found on this call",
              )}
              <p className="border-t border-line px-3 py-2 text-meta text-ink-2">{AS_STATED}</p>
            </Gated>
          </Panel>

          {policyOpen && (sets.unattributed.length > 0 || partial) && (
            <Panel
              title={UNKNOWN_ROLE_HEADING}
              titleId="panel-call-unattributed"
              subtitle="Forward-looking figures from speakers whose role couldn't be read. Not attributed to management, not scored and not in any hit rate. AI-extracted."
              qualifier={<Qualifier>{AI_EXTRACTED}</Qualifier>}
              variant="flush"
              footer={foot(call)}
            >
              <Gated policies={calls.policies} field={FIGURES_FIELD} heldBackNote={FIGURES_HELD} showLabel={false}>
                {tableOf(
                  sets.unattributed,
                  `${company.name}: forward-looking figures on the ${call.fiscal_label} call from speakers whose role couldn't be read`,
                  "Figure as stated",
                  "No forward-looking figures found in the turns whose speaker couldn't be read",
                )}
              </Gated>
            </Panel>
          )}
        </>
      )}

      <GuidanceRecord data={calls} name={name} companyName={company.name} foot={foot(call)} />
    </div>
  );
}
