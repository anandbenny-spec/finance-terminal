// The meeting page (C4; Spec 01 §4.3, §4.5; Design 01 §5.3), for /policy/fed/[date] and /policy/rbi/[date].
// Header: breadcrumb, "FOMC meeting · {date}" or "MPC meeting no. {n} · {date}", DecisionLine · tally · release time
// (the Fed's adds IST) · the official document (the Fed's only: the RBI's are named, never linked: Anand, 24 Oct
// 2026) · previous and next meeting. Tabs in the URL (?tab=…): Overview, What changed, Statement, Minutes, Press
// conference Market reaction (Pass 1b, #75). Every figure is a row an `api` view returned; every empty case
// is §4.5's copy; no hawk/dove UI (D9). The 2018 baselines get pages too.
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { AIBlock } from "@/components/ai/AIBlock";
import { Cited } from "@/components/ai/CitationChip";
import { DecisionLine } from "@/components/ai/DecisionLine";
import { DocumentView } from "@/components/ai/DocumentView";
import { KeyChanges } from "@/components/ai/KeyChanges";
import { Redline } from "@/components/ai/Redline";
import { NumbersChanged } from "@/components/ai/NumbersChanged";
import { VotesPanel } from "@/components/policy/VotesPanel";
import { MeetingReactionView } from "@/components/policy/MeetingReaction";
import { Freshness } from "@/components/data/Freshness";
import { Gated, fieldPolicy, type FieldPolicies } from "@/components/data/Gated";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel } from "@/components/data/Panel";
import { Qualifier } from "@/components/data/Qualifier";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { Tabs } from "@/components/data/Tabs";
import { FEEDS, type TaskKey } from "@/lib/feeds";
import { formatDate, formatReleaseTime } from "@/lib/format";
import { stanceCitations, summaryBlock } from "@/lib/policy/ai";
import { TABS, decisionLine, meetingHref, meetingTabHref, stanceText, tabOf, tally, type TabId } from "@/lib/policy/decisions";
import { documentSource, documentTitle } from "@/lib/policy/document";
import {
  DOC_TYPES,
  ZONE,
  governorNote,
  latestByType,
  meetingTitle,
  minutesNote,
  noDecisionNote,
  projectionRows,
  rateRows,
  releaseLine,
  speakerAnchors,
  transcriptNote,
} from "@/lib/policy/meeting";
import { changesModel, docDateOf, numbersModel } from "@/lib/policy/changes";
import { meetingReaction } from "@/lib/policy/reaction";
import { paragraphAt, paragraphs, textFromChunks, type Paragraph } from "@/lib/policy/text";
import type { DocumentChunkRow, DocumentRow, PolicyMeetingRow } from "@/lib/queries/api";
import { readDocumentChunks, readMeetingDiff, readMeetingPage, readMeetingReaction, readNumbersBase, type CitedOutput, type MeetingPage } from "@/lib/queries/meeting";
import type { Bank } from "@/lib/queries/policy";
import { readFreshness } from "@/lib/queries/shell";
import { panelFreshness, type PanelFreshness } from "@/lib/shell/freshness";

// The tabs and `tabOf` live in lib/policy/decisions.ts since #147 (the /latest routes and PolicyCard use them too);
// re-exported so the meeting pages and their tests import them from here, as before.
export { TABS, tabOf };
export type { TabId };

const ERROR = "Couldn't load this panel. Try again.";

type Ctx = {
  bank: Bank;
  m: PolicyMeetingRow;
  page: MeetingPage;
  docs: Map<string, DocumentRow>;
  policies: FieldPolicies | null;
  fresh: (dataTo: string | null) => PanelFreshness;
  /** Freshness from other tasks than the bank's documents (the Market reaction tab's policy.reactions). */
  freshOf: (tasks: readonly TaskKey[], dataTo: string | null) => PanelFreshness;
};

/** The day a document was published, in the bank's zone (its "Data to"), else the meeting's date. */
function docDate(ctx: Ctx, d: DocumentRow | null | undefined): string {
  return docDateOf(ctx.bank, ctx.m, d);
}

/** A document's source line: the Fed's attribution; the RBI's names the document, its date and RBI's press-release
 * number (never a link: Brief §6.2). The /documents page's rule (lib/policy/document.ts), so the two always agree. */
function docSource(ctx: Ctx, d: DocumentRow | null | undefined): string {
  const attribution = d?.attribution ?? ctx.m.attribution ?? (ctx.bank === "Fed" ? "Source: Federal Reserve Board" : "Source: Reserve Bank of India");
  if (!d) return attribution;
  return documentSource({ ...d, attribution }, ctx.bank);
}

function officialLink(ctx: Ctx, d: DocumentRow | null | undefined, text = "Official source ↗"): ReactNode {
  if (ctx.bank !== "Fed" || !d?.source_url?.startsWith("https://")) return null;
  return (
    <a href={d.source_url} className="text-meta text-ink-1 underline" rel="noreferrer" target="_blank">
      {text}
    </a>
  );
}

function Foot({ ctx, sources, dataTo }: { ctx: Ctx; sources: string[]; dataTo: string | null }) {
  return (
    <>
      <SourceLine lines={sources} />
      <Freshness freshness={ctx.fresh(dataTo)} />
    </>
  );
}

function summaryOf(ctx: Ctx, d: DocumentRow | null | undefined): CitedOutput | null {
  if (!d || !ctx.page.ai.ok) return null;
  return ctx.page.ai.summaries[d.id] ?? null;
}

function Summary({ ctx, doc, label = "AI summary" }: { ctx: Ctx; doc: DocumentRow | null | undefined; label?: string }) {
  const s = summaryOf(ctx, doc);
  const block = s ? summaryBlock(s.output, s.citations, { linkOfficial: ctx.bank === "Fed", label }) : null;
  if (!ctx.page.ai.ok) return <StateNote kind="error">Couldn&apos;t load the AI summary.</StateNote>;
  if (!block || !ctx.policies) return <StateNote kind="unavailable">No AI summary of this document yet.</StateNote>;
  return (
    <Gated policies={ctx.policies} field="ai.doc_summary" showLabel={false}>
      <AIBlock block={block} now={ctx.page.readAt} dense={false} />
    </Gated>
  );
}

async function documentText(id: number): Promise<{ text: string; paras: Paragraph[]; chunks: DocumentChunkRow[] } | null> {
  const read = await readDocumentChunks(id);
  if (!read.ok) return null;
  try {
    const text = textFromChunks(read.chunks);
    return { text, paras: paragraphs(text), chunks: read.chunks };
  } catch (error) {
    console.error(`[meeting] document ${id}: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

function MeetingTabs({ bank, date, tab }: { bank: Bank; date: string; tab: TabId }) {
  return <Tabs label="Meeting sections" items={TABS.map((t) => ({ id: t.id, label: t.label, href: meetingTabHref(bank, date, t.id) }))} current={tab} />;
}

// ------------------------------------------------------------------ Overview

async function Overview({ ctx }: { ctx: Ctx }) {
  const { bank, m, page } = ctx;
  const fed = bank === "Fed";
  const decisionDoc = ctx.docs.get(DOC_TYPES[bank].decision) ?? null;
  const decisionText = decisionDoc ? await documentText(decisionDoc.id) : null;
  const paras = decisionText?.paras ?? [];
  const settings = page.settings.ok ? page.settings.rows : null;
  const statementHref = (n: number) => `${meetingTabHref(bank, m.decision_date, "statement")}#p-${n}`;
  const decisionSource = docSource(ctx, decisionDoc);
  const decisionFoot = <Foot ctx={ctx} sources={[decisionSource]} dataTo={docDate(ctx, decisionDoc)} />;

  // Fed: the decision-and-guidance paragraph (D2) is the block holding the target-range sentence (the upper bound's span).
  const upper = settings?.find((s) => s.instrument === "fed_target_upper") ?? null;
  const guidance = fed && upper && upper.document_id === decisionDoc?.id ? paragraphAt(paras, upper.char_start) : null;
  const stance = page.ai.ok ? page.ai.stance : null;
  const stanceChips = stance ? stanceCitations(stance.output, stance.citations) : [];

  const rates = settings ? rateRows(settings, paras, decisionDoc?.id ?? null) : null;
  const projections = page.projections.ok ? projectionRows(page.projections.rows, paras, decisionDoc?.id ?? null) : null;
  const market = fed ? "US" : "IN";

  return (
    <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-12">
      <div className="flex min-w-0 flex-col gap-4 xl:col-span-8">
        <Panel title={fed ? "The decision" : "The decision and stance"} titleId="panel-decision" subtitle="What did the bank decide, and why?" market={market} footer={decisionFoot}>
          {!decisionDoc ? (
            <StateNote kind="unavailable">The decision document isn&apos;t on the site yet.</StateNote>
          ) : (
            <div className="flex flex-col gap-4">
              <Summary ctx={ctx} doc={decisionDoc} />
              {fed && guidance && (
                <figure>
                  <figcaption className="mb-1.5 text-meta text-ink-2">The statement&apos;s decision and guidance</figcaption>
                  <blockquote className="max-w-(--reading-width) border-l-2 border-line-strong pl-3 text-read text-ink-1">
                    {guidance.text}{" "}
                    <a href={statementHref(guidance.n)} className="font-mono text-meta whitespace-nowrap text-ink-3 no-underline hover:text-ink-1">
                      ¶{guidance.n}
                    </a>
                  </blockquote>
                </figure>
              )}
              {!fed && (
                <p className="text-data">
                  <span className="text-ink-2">Stance: </span>
                  {!page.ai.ok ? (
                    <StateNote kind="error">Couldn&apos;t load the stance.</StateNote>
                  ) : m.stance && ctx.policies ? (
                    <Gated policies={ctx.policies} field="ai.stance_extraction">
                      {stanceChips.length ? <Cited text={stanceText(m.stance)} citations={stanceChips} /> : stanceText(m.stance)}
                    </Gated>
                  ) : (
                    <StateNote kind="unavailable">The stance isn&apos;t extracted yet.</StateNote>
                  )}
                </p>
              )}
            </div>
          )}
        </Panel>

        <VotesPanel bank={bank} m={m} votes={page.votes.ok ? page.votes.rows : null} footer={decisionFoot} />

        <Panel title="Rates set at this meeting" titleId="panel-rates" subtitle="What did each rate become, and by how much did it move?" market={market} variant="flush" footer={decisionFoot}>
          {rates === null ? (
            <div className="p-3"><StateNote kind="error">{ERROR}</StateNote></div>
          ) : rates.length === 0 ? (
            <div className="p-3"><StateNote kind="unavailable">No rates are stored for this meeting yet.</StateNote></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-data">
                <caption className="sr-only">Rates set at the {meetingTitle(m)}</caption>
                <thead className="bg-raised">
                  <tr>
                    <th scope="col" className="h-8 border-b border-line-strong px-3 text-left text-meta font-medium text-ink-2">Instrument</th>
                    <th scope="col" className="h-8 border-b border-line-strong px-3 text-right text-meta font-medium text-ink-2">Rate</th>
                    <th scope="col" className="h-8 border-b border-line-strong px-3 text-right text-meta font-medium text-ink-2">Change</th>
                    <th scope="col" className="h-8 border-b border-line-strong px-3 text-left text-meta font-medium text-ink-2">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {rates.map((r) => (
                    <tr key={r.key} className="border-b border-line last:border-b-0">
                      <th scope="row" className="px-3 py-1.5 text-left align-top font-normal text-ink-1">
                        {r.label}
                        {r.note && <span className="block text-meta text-ink-3">{r.note}</span>}
                      </th>
                      <td className="px-3 py-1.5 text-right align-top whitespace-nowrap">{r.value}</td>
                      <td className="px-3 py-1.5 text-right align-top whitespace-nowrap">{r.change}</td>
                      <td className="px-3 py-1.5 align-top">
                        {r.para !== null ? (
                          <a href={statementHref(r.para)} className="font-mono text-meta text-ink-3 no-underline hover:text-ink-1">¶{r.para}</a>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!fed && <p className="border-t border-line px-3 py-2 text-meta text-ink-3">Corridor rates as set at MPC meetings.</p>}
            </div>
          )}
        </Panel>

        {!fed && (
          <Panel title="Projections" titleId="panel-projections" subtitle="What did the RBI project for inflation and growth?" market="IN" variant="flush" footer={decisionFoot}>
            {projections === null ? (
              <div className="p-3"><StateNote kind="error">{ERROR}</StateNote></div>
            ) : projections.length === 0 ? (
              <div className="p-3">
                <StateNote kind="unavailable">No projections are stored for this resolution.</StateNote>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-data">
                  <caption className="sr-only">Projections in the resolution of {formatDate(m.decision_date)}</caption>
                  <thead className="bg-raised">
                    <tr>
                      <th scope="col" className="h-8 border-b border-line-strong px-3 text-left text-meta font-medium text-ink-2">Projection</th>
                      <th scope="col" className="h-8 border-b border-line-strong px-3 text-left text-meta font-medium text-ink-2">Period</th>
                      <th scope="col" className="h-8 border-b border-line-strong px-3 text-right text-meta font-medium text-ink-2">Value</th>
                      <th scope="col" className="h-8 border-b border-line-strong px-3 text-left text-meta font-medium text-ink-2">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {projections.map((p) => (
                      <tr key={p.key} className="h-8 border-b border-line last:border-b-0">
                        <th scope="row" className="px-3 text-left font-normal text-ink-1">{p.variable}</th>
                        <td className="px-3 whitespace-nowrap">{p.period}</td>
                        <td className="px-3 text-right whitespace-nowrap">{p.value}</td>
                        <td className="px-3">
                          {p.label ? <Qualifier>{p.label}</Qualifier> : null}
                          {p.para !== null && (
                            <a href={statementHref(p.para)} className="ml-1.5 font-mono text-meta text-ink-3 no-underline hover:text-ink-1">¶{p.para}</a>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        )}
      </div>

      <div className="min-w-0 xl:col-span-4">
        <Rail ctx={ctx} />
      </div>
    </div>
  );
}

function Rail({ ctx }: { ctx: Ctx }) {
  const { bank, m, page } = ctx;
  const docs = [...ctx.docs.values()].sort((a, b) => (a.published_at ?? "").localeCompare(b.published_at ?? ""));
  const next = page.adjacent.ok ? page.adjacent.next : null;
  const facts: [string, ReactNode][] = [
    ["Kind", m.kind === "unscheduled" ? "Unscheduled" : m.kind === "special" ? "Special" : "Scheduled"],
    ["Decision", formatDate(m.decision_date)],
  ];
  if (m.decision_at) facts.push(["Released", <span key="r" className="font-mono">{formatReleaseTime(m.decision_at, ZONE[bank])}</span>]);
  if (m.minutes_due_on) facts.push(["Minutes due", formatDate(m.minutes_due_on)]);
  return (
    <Panel title="Key facts" titleId="panel-facts" footer={<Foot ctx={ctx} sources={[docSource(ctx, null)]} dataTo={m.decision_date} />}>
      <dl className="flex flex-col gap-1 text-data">
        {facts.map(([k, v]) => (
          <div key={k} className="flex gap-3">
            <dt className="w-24 shrink-0 text-ink-2">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <h4 className="mt-4 mb-1.5 text-data font-semibold text-ink-1">Documents</h4>
      {!page.documents.ok ? (
        <StateNote kind="error">{ERROR}</StateNote>
      ) : docs.length === 0 ? (
        <StateNote kind="unavailable">No documents are on the site yet.</StateNote>
      ) : (
        <ul className="flex flex-col gap-1.5 text-data">
          {docs.map((d) => (
            <li key={d.id}>
              <span className="text-ink-1">{d.doc_type_label}</span>
              <span className="block text-meta text-ink-3">{docSource(ctx, d)}{bank === "Fed" ? ` · ${formatDate(docDate(ctx, d))}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
      <h4 className="mt-4 mb-1.5 text-data font-semibold text-ink-1">Next meeting</h4>
      {!page.adjacent.ok ? (
        <StateNote kind="error">{ERROR}</StateNote>
      ) : next ? (
        <Link href={meetingHref(bank, next.decision_date)} className="text-data text-ink-1 underline">
          {meetingTitle(next)}
        </Link>
      ) : (
        <StateNote kind="unavailable">The next meeting isn&apos;t scheduled yet.</StateNote>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ What changed

async function Changes({ ctx }: { ctx: Ctx }) {
  const decisionDoc = ctx.docs.get(DOC_TYPES[ctx.bank].decision) ?? null;
  const title = ctx.bank === "Fed" ? "What changed in the statement" : "What changed in the resolution";
  const panel = (body: ReactNode, sources: string[] = [docSource(ctx, decisionDoc)]) => (
    <Panel title={title} titleId="panel-changes" subtitle="What did this decision change in the wording, against the one before?" variant="prose" footer={<Foot ctx={ctx} sources={sources} dataTo={docDate(ctx, decisionDoc)} />}>
      {body}
    </Panel>
  );
  if (!decisionDoc) return <div className="mt-5">{panel(<StateNote kind="unavailable">The decision document isn&apos;t on the site yet.</StateNote>)}</div>;
  const read = await readMeetingDiff(decisionDoc.id);
  if (!read.ok) return <div className="mt-5">{panel(<StateNote kind="error">{ERROR}</StateNote>)}</div>;
  if (!read.diff) {
    return (
      <div className="mt-5">
        {panel(<StateNote kind="unavailable">There&apos;s no earlier {ctx.bank === "Fed" ? "statement" : "resolution"} on the site to compare this one with.</StateNote>)}
      </div>
    );
  }
  const model = changesModel({ bank: ctx.bank, m: ctx.m, decisionDoc, diff: read.diff, base: read.base, summary: read.summary });
  return (
    <div className="mt-5">
      {panel(
        <div className="flex flex-col gap-5">
          <p className="text-data text-ink-2">Compared with the {model.baseLabel}.</p>
          {model.summary && ctx.policies ? (
            <Gated policies={ctx.policies} field="ai.statement_diff_summary" showLabel={false}>
              <AIBlock block={{ ...model.summary, label: "What changed" }} now={ctx.page.readAt} dense={false} />
            </Gated>
          ) : (
            <StateNote kind="unavailable">No AI summary of the changes yet.</StateNote>
          )}
          <NumbersPart ctx={ctx} base={read.base} />
          {model.keys && model.redline ? (
            <KeyChanges
              model={model.keys}
              before={model.before}
              now={model.now}
              unchangedParagraphs={model.redline.unchangedParagraphs}
              redline={<Redline model={model.redline} added={model.wordsAdded} removed={model.wordsRemoved} />}
            />
          ) : (
            <StateNote kind="unavailable">The redline isn&apos;t available for this document.</StateNote>
          )}
        </div>,
        [docSource(ctx, decisionDoc), ...(read.base ? [docSource(ctx, read.base)] : [])],
      )}
    </div>
  );
}

/** NumbersChanged against the meeting the diff's base document belongs to (the same target period across vintages). */
async function NumbersPart({ ctx, base }: { ctx: Ctx; base: DocumentRow | null }) {
  const { m, bank, page } = ctx;
  const heading = <h3 className="text-title text-ink-1">Numbers that changed</h3>;
  if (!base?.event_slug) return null;
  const earlier = page.settings.ok && page.votes.ok && page.projections.ok ? await readNumbersBase(bank, base.event_slug) : null;
  const stancePolicy = ctx.policies ? fieldPolicy(ctx.policies, "ai.stance_extraction") : null;
  const model = numbersModel({
    m,
    base,
    current: page.settings.ok && page.votes.ok && page.projections.ok ? { settings: page.settings.rows, votes: page.votes.rows, projections: page.projections.rows } : null,
    earlier: earlier && earlier.ok ? earlier : null,
    stance: { show: stancePolicy !== null && stancePolicy.status !== "hidden", label: stancePolicy?.status === "labelled" ? stancePolicy.label : null },
  });
  if (model.kind === "none") return null;
  return (
    <section className="flex flex-col gap-2">
      {heading}
      {model.kind === "error" ? (
        <StateNote kind="error">{ERROR}</StateNote>
      ) : model.kind === "unavailable" ? (
        <StateNote kind="unavailable">The numbers of the two decisions can&apos;t be compared yet.</StateNote>
      ) : (
        <NumbersChanged items={model.items} previousDate={model.previousDate} newDate={model.newDate} />
      )}
    </section>
  );
}

// ------------------------------------------------------------------ Market reaction (Pass 1b, #75)

/** Reactions are computed for decisions since 2019 (Spec 01 §2); the 2018 baselines have none. */
const FIRST_REACTION_DATE = "2019-01-01";

async function MarketReaction({ ctx }: { ctx: Ctx }) {
  const { bank, m } = ctx;
  const panel = (body: ReactNode) => (
    <div className="mt-5">
      <Panel title="Market reaction" titleId="panel-reaction" subtitle="How did markets move when the decision came out?" market={bank === "Fed" ? "US" : "IN"} footer={<Foot ctx={ctx} sources={[docSource(ctx, null)]} dataTo={m.decision_date} />}>
        {body}
      </Panel>
    </div>
  );
  if (m.decision_date < FIRST_REACTION_DATE) {
    return panel(<StateNote kind="unavailable">Market reactions are shown for decisions since 2019.</StateNote>);
  }
  const read = await readMeetingReaction(bank, m.slug);
  if (!read.ok) return panel(<StateNote kind="error">{ERROR}</StateNote>);
  const model = meetingReaction({
    bank,
    decisionDate: m.decision_date,
    today: ctx.page.today,
    reactions: read.reactions,
    series: read.series,
    observations: read.observations,
  });
  const footer = (
    <>
      <SourceLine lines={model.sources} />
      <Freshness freshness={ctx.freshOf(FEEDS.policyReactions, model.dataTo)} />
    </>
  );
  return <MeetingReactionView model={model} footer={footer} refresh={FEEDS.policyReactions} />;
}

// ------------------------------------------------------------------ Statement, Minutes, Press conference

async function DocumentTab({
  ctx,
  doc,
  title,
  missing,
  withSummary,
  speakers,
  switcher,
}: {
  ctx: Ctx;
  doc: DocumentRow | null;
  title: string;
  missing: string;
  withSummary: boolean;
  speakers?: boolean;
  switcher?: ReactNode;
}) {
  const body = doc ? await documentText(doc.id) : null;
  const anchors = body && speakers ? speakerAnchors(body.chunks, body.paras) : [];
  return (
    <div className="mt-5 flex flex-col gap-4">
      {switcher}
      <Panel
        title={doc ? documentTitle(doc) : title}
        titleId="panel-document"
        actions={officialLink(ctx, doc)}
        variant="prose"
        footer={<Foot ctx={ctx} sources={[docSource(ctx, doc)]} dataTo={doc ? docDate(ctx, doc) : null} />}
      >
        {!ctx.page.documents.ok ? (
          <StateNote kind="error">{ERROR}</StateNote>
        ) : !doc ? (
          <StateNote kind="unavailable">{missing}</StateNote>
        ) : !body ? (
          <StateNote kind="error">{ERROR}</StateNote>
        ) : (
          <div className="flex flex-col gap-5">
            {withSummary && <Summary ctx={ctx} doc={doc} />}
            {anchors.length > 0 && (
              <nav aria-label="Members' statements" className="text-data">
                <h4 className="mb-1.5 font-semibold text-ink-1">Members&apos; statements</h4>
                <ul className="flex flex-wrap gap-x-4 gap-y-1">
                  {anchors.map((a, i) => (
                    <li key={a.speaker}>
                      <a href={`#member-${i + 1}`} className="text-ink-1 underline">{a.speaker}</a>
                    </li>
                  ))}
                </ul>
              </nav>
            )}
            <DocumentView
              paragraphs={body.paras}
              anchors={Object.fromEntries(anchors.map((a, i) => [a.para, `member-${i + 1}`]))}
              label={documentTitle(doc)}
            />
          </div>
        )}
      </Panel>
    </div>
  );
}

function StatementSwitch({ ctx, which }: { ctx: Ctx; which: "resolution" | "governor" }) {
  const items = [
    { id: "resolution", label: "Resolution" },
    { id: "governor", label: "Governor's statement" },
  ] as const;
  return (
    <div role="group" aria-label="Document" className="flex w-max overflow-hidden rounded-md border border-line">
      {items.map((x) => (
        <Link
          key={x.id}
          href={meetingTabHref(ctx.bank, ctx.m.decision_date, "statement", x.id === "governor" ? "doc=governor" : "")}
          aria-current={which === x.id ? "page" : undefined}
          className="border-r border-line px-2.5 py-1 text-meta text-ink-2 no-underline last:border-r-0 hover:text-ink-1 aria-[current=page]:bg-active aria-[current=page]:text-ink-1"
        >
          {x.label}
        </Link>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ The page

type Neighbour = Pick<PolicyMeetingRow, "decision_date">;

/** The header's Previous / Next links. Both keep the tab being read, so ← Previous on What changed lands on the
 * previous meeting's What changed (#147); other query parameters (`doc=`) are dropped. */
export function OtherMeetings({ bank, tab, previous, next }: { bank: Bank; tab: TabId; previous: Neighbour | null; next: Neighbour | null }) {
  return (
    <nav aria-label="Other meetings" className="flex flex-wrap gap-x-4 gap-y-1">
      {previous && (
        <Link href={meetingTabHref(bank, previous.decision_date, tab)} className="text-ink-3 underline decoration-line-strong hover:text-ink-1 hover:decoration-highlight">
          ← Previous: {formatDate(previous.decision_date)}
        </Link>
      )}
      {next && (
        <Link href={meetingTabHref(bank, next.decision_date, tab)} className="text-ink-3 underline decoration-line-strong hover:text-ink-1 hover:decoration-highlight">
          Next: {formatDate(next.decision_date)} →
        </Link>
      )}
    </nav>
  );
}

function Header({ ctx, tab, tabs }: { ctx: Ctx; tab: TabId; tabs: ReactNode }) {
  const { bank, m, page } = ctx;
  const line = m.status === "held" && m.is_rate_decision ? decisionLine(m) : null;
  const t = m.status === "held" && page.votes.ok ? tally(m, page.votes.rows) : null;
  // A special meeting (MPC no. 39) released no decision, so it has no release line.
  const release = m.status === "held" && m.is_rate_decision && m.kind !== "special" ? releaseLine(m) : null;
  const decisionDoc = ctx.docs.get(DOC_TYPES[bank].decision) ?? null;
  const prev = page.adjacent.ok ? page.adjacent.previous : null;
  const next = page.adjacent.ok ? page.adjacent.next : null;
  return (
    <>
      <PageHeader
        crumbs={["Macro and policy", { label: "Fed vs RBI", href: "/policy" }, bank === "Fed" ? "FOMC" : "MPC"]}
        title={meetingTitle(m)}
        description={
          <div className="flex flex-col gap-1.5">
            {line && <DecisionLine text={line} />}
            <p className="flex flex-wrap gap-x-3 gap-y-1 text-data text-ink-2">
              {t?.kind === "tally" && <span>{t.line}</span>}
              {release && <span className="font-mono">{release}</span>}
              {officialLink(ctx, decisionDoc, bank === "Fed" ? "Official statement ↗" : undefined)}
            </p>
          </div>
        }
        meta={<OtherMeetings bank={bank} tab={tab} previous={prev} next={next} />}
      />
      {tabs}
    </>
  );
}

export async function MeetingView({ bank, slug, date, tab, doc }: { bank: Bank; slug: string; date: string; tab: TabId; doc: string | undefined }) {
  const [page, freshRead] = await Promise.all([readMeetingPage(bank, slug, date), readFreshness()]);
  if (!page.meeting.ok) {
    return (
      <Panel title="Meeting" titleId="panel-meeting">
        <StateNote kind="error">{ERROR}</StateNote>
      </Panel>
    );
  }
  const m = page.meeting.row;
  if (!m || m.bank !== bank) notFound();
  const tasks = bank === "Fed" ? FEEDS.meetingFed : FEEDS.meetingRbi;
  const ctx: Ctx = {
    bank,
    m,
    page,
    docs: latestByType(page.documents.ok ? page.documents.rows : []),
    policies: page.fieldPolicy.ok ? page.fieldPolicy.rows : null,
    fresh: (dataTo) => panelFreshness(freshRead.ok ? freshRead.tasks : null, tasks, dataTo, page.readAt),
    freshOf: (of, dataTo) => panelFreshness(freshRead.ok ? freshRead.tasks : null, of, dataTo, page.readAt),
  };

  // A meeting without a decision gets its header and §4.5's note, no tabs.
  if (m.status !== "held" || m.kind === "special" || !m.is_rate_decision) {
    const note = noDecisionNote(m);
    return (
      <>
        <Header ctx={ctx} tab={tab} tabs={null} />
        <div className="mt-5">
          <Panel title="This meeting" titleId="panel-meeting" market={bank === "Fed" ? "US" : "IN"} footer={<Foot ctx={ctx} sources={[docSource(ctx, null)]} dataTo={null} />}>
            <StateNote kind={m.status === "scheduled" ? "unavailable" : "empty"}>{note}</StateNote>
          </Panel>
        </div>
      </>
    );
  }

  const types = DOC_TYPES[bank];
  let body: ReactNode;
  if (tab === "overview") body = <Overview ctx={ctx} />;
  else if (tab === "changes") body = <Changes ctx={ctx} />;
  else if (tab === "statement") {
    const governor = bank === "RBI" && doc === "governor";
    const d = ctx.docs.get(governor && types.governor ? types.governor : types.decision) ?? null;
    body = (
      <DocumentTab
        ctx={ctx}
        doc={d}
        title={governor ? "Governor's statement" : bank === "Fed" ? "FOMC statement" : "MPC resolution"}
        missing={governor ? governorNote(m) : "The decision document isn't on the site yet."}
        withSummary={governor}
        switcher={bank === "RBI" ? <StatementSwitch ctx={ctx} which={governor ? "governor" : "resolution"} /> : undefined}
      />
    );
  } else if (tab === "minutes") {
    body = (
      <DocumentTab ctx={ctx} doc={ctx.docs.get(types.minutes) ?? null} title={bank === "Fed" ? "FOMC minutes" : "MPC minutes"} missing={minutesNote(m, page.today)} withSummary speakers={bank === "RBI"} />
    );
  } else if (tab === "reaction") {
    body = <MarketReaction ctx={ctx} />;
  } else {
    body = (
      <DocumentTab ctx={ctx} doc={ctx.docs.get(types.transcript) ?? null} title="Press conference transcript" missing={transcriptNote(m, page.today)} withSummary />
    );
  }

  return (
    <>
      <Header ctx={ctx} tab={tab} tabs={<MeetingTabs bank={bank} date={m.decision_date} tab={tab} />} />
      {body}
    </>
  );
}
