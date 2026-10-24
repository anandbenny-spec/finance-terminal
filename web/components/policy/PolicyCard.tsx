// PolicyCard (Spec 01 D13, §4.2 "Latest decisions"): a bank's latest decision. DecisionLine; the date and release
// time; the tally line; the RBI's stance with its quote chip (AI-extracted, gated by field policy); the AI summary of
// the decision document; links to the meeting page and, for the Fed only, the official statement. The RBI's
// documents are named, never linked (Anand, 24 Oct 2026; Brief §6.2). Reused on the landing page later (D13).
import Link from "next/link";
import type { ReactNode } from "react";
import { AIBlock } from "@/components/ai/AIBlock";
import { Cited } from "@/components/ai/CitationChip";
import { DecisionLine } from "@/components/ai/DecisionLine";
import { Gated, type FieldPolicies } from "@/components/data/Gated";
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { formatDate, formatReleaseTime } from "@/lib/format";
import { stanceCitations, summaryBlock } from "@/lib/policy/ai";
import { decisionLine, meetingHref, stanceText, tally } from "@/lib/policy/decisions";
import type { LatestDecision } from "@/lib/queries/overview";
import type { Bank } from "@/lib/queries/policy";

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex gap-4">
      <dt className="w-20 shrink-0 text-ink-2">{term}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

export function PolicyCard({
  bank,
  latest,
  policies,
  now,
  footer,
}: {
  bank: Bank;
  latest: LatestDecision | null;
  policies: FieldPolicies | null;
  now: string;
  footer: ReactNode;
}) {
  const fed = bank === "Fed";
  const m = latest?.meeting ?? null;
  const panel = (children: ReactNode, date?: string) => (
    <Panel
      title={fed ? "Latest Fed decision" : "Latest RBI decision"}
      titleId={fed ? "panel-latest-fed" : "panel-latest-rbi"}
      qualifier={date ? <span className="text-meta text-ink-3">{formatDate(date)}</span> : undefined}
      subtitle={fed ? "What did the Fed just decide, and why?" : "What did the RBI just decide, and why?"}
      market={fed ? "US" : "IN"}
      span={6}
      footer={footer}
    >
      {children}
    </Panel>
  );
  if (latest === null) return panel(<StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>);
  if (m === null) return panel(<StateNote kind="unavailable">No decision is published yet.</StateNote>);

  const line = decisionLine(m);
  const t = tally(m, latest.votes);
  const zone = fed ? "America/New_York" : "Asia/Kolkata";
  const summary = latest.summary
    ? summaryBlock(latest.summary.output, latest.summary.citations, { linkOfficial: fed, label: "AI summary" })
    : null;
  const stanceCites = latest.stance ? stanceCitations(latest.stance.output, latest.stance.citations) : [];
  const officialUrl = fed && latest.document?.source_url?.startsWith("https://") ? latest.document.source_url : null;

  return panel(
    <>
      {line ? <DecisionLine text={line} /> : <StateNote kind="unavailable">The decision&apos;s rate isn&apos;t published yet.</StateNote>}
      <dl className="mt-2.5 flex flex-col gap-1 text-data">
        {m.decision_at && <Row term={fed ? "Statement" : "Resolution"}><span className="font-mono">{formatReleaseTime(m.decision_at, zone)}</span></Row>}
        <Row term="Vote">
          {t.kind === "tally" ? t.line : t.kind === "pending" ? <StateNote kind="unavailable">{t.note}</StateNote> : <StateNote kind="unavailable">Votes aren&apos;t published yet.</StateNote>}
        </Row>
        {!fed && (
          <Row term="Stance">
            {m.stance && policies ? (
              <Gated policies={policies} field="ai.stance_extraction">
                {stanceCites.length > 0 ? <Cited text={stanceText(m.stance)} citations={stanceCites} /> : stanceText(m.stance)}
              </Gated>
            ) : (
              <StateNote kind="unavailable">The stance isn&apos;t extracted yet.</StateNote>
            )}
          </Row>
        )}
      </dl>
      <div className="mt-3.5 border-t border-line pt-3">
        {summary && policies ? (
          <Gated policies={policies} field="ai.doc_summary" showLabel={false}>
            <AIBlock block={summary} now={now} />
          </Gated>
        ) : (
          <StateNote kind="unavailable">No AI summary of this decision yet.</StateNote>
        )}
      </div>
      <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-data">
        <Link href={meetingHref(bank, m.decision_date)} className="text-ink-1 underline">
          {fed ? "FOMC meeting" : `MPC meeting${m.meeting_no ? ` no. ${m.meeting_no}` : ""}`}, {formatDate(m.decision_date)}
        </Link>
        {officialUrl && (
          <a href={officialUrl} className="text-ink-1 underline" rel="noreferrer" target="_blank">
            Official statement ↗
          </a>
        )}
      </p>
    </>,
    m.decision_date,
  );
}
