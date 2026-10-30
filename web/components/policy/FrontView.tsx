// The front page (/, #152; Design 01 §1.17; Brief §10): leads with what sets the site apart. In order: the one-line
// positioning with three lines on how the site works and the way into the tracker; What changed at the latest FOMC
// and MPC (the bank's own words, compared sentence by sentence, with the first three Key changes); how the members
// voted; how much each statement changed since 2019 (ChangeStrip); and where rates stand. The first version of
// "Start here". Every figure and sentence is a row the database returned (lib/queries/front.ts); the three hero lines
// are fixed copy about how the site works, not data. Each bank's part fails on its own, and no panel is ever blank.
import { Check } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { AIBlock } from "@/components/ai/AIBlock";
import { ChangeStrip } from "@/components/ai/ChangeStrip";
import { DecisionLine } from "@/components/ai/DecisionLine";
import { KeyChanges } from "@/components/ai/KeyChanges";
import { NumbersChanged } from "@/components/ai/NumbersChanged";
import { Gated } from "@/components/data/Gated";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { Section } from "@/components/data/SectionHeading";
import { StateNote } from "@/components/data/StateNote";
import { ErrorPanel, Foot, latestOf } from "@/components/policy/PanelFoot";
import { PendingVote } from "@/components/policy/PendingVote";
import { Row } from "@/components/policy/PolicyCard";
import { RateStats } from "@/components/policy/RateStats";
import { VotesPanel } from "@/components/policy/VotesPanel";
import { Button } from "@/components/ui/button";
import { FEEDS, type TaskKey } from "@/lib/feeds";
import { formatDate, formatReleaseTime } from "@/lib/format";
import { buildChangeStrips, stripScale } from "@/lib/policy/changestrip";
import { decisionLine, meetingTabHref, tally } from "@/lib/policy/decisions";
import { pressReleaseLabel } from "@/lib/policy/document";
import { ZONE } from "@/lib/policy/meeting";
import { SITE_TAGLINE } from "@/lib/site";
import type { BankChanges, Front } from "@/lib/queries/front";
import type { LatestDecision } from "@/lib/queries/overview";
import { POLICY_START, type Bank } from "@/lib/queries/policy";
import type { Read } from "@/lib/queries/shell";

const RBI_SOURCE = "Source: Reserve Bank of India";
const HERO_LINES = ["An AI line that can't be cited isn't shown", "Every figure names its source and date", "End-of-day data, checked daily"] as const;

type Policies = Parameters<typeof Gated>[0]["policies"];

function Hero({ year }: { year: string }) {
  return (
    <header className="max-w-(--reading-width)">
      <p className="font-mono text-meta text-ink-3">Start here</p>
      <h1 className="mt-1.5 text-page text-ink-1">{SITE_TAGLINE}</h1>
      <p className="mt-2 text-read text-ink-2">
        Every FOMC and RBI decision since {year}: what each bank decided, what changed in its statement sentence by sentence, and how each member voted. Every AI
        line links to the passage it came from.
      </p>
      <ul className="mt-3 flex flex-col gap-1">
        {HERO_LINES.map((line) => (
          <li key={line} className="flex items-center gap-2 text-data text-ink-2">
            <Check aria-hidden className="size-4 shrink-0 text-ink-3" />
            {line}
          </li>
        ))}
      </ul>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button asChild size="lg">
          <Link href="/policy">Open the Fed vs RBI tracker</Link>
        </Button>
        <Button asChild size="lg" variant="outline">
          <Link href="/policy#history">Every decision since {year}</Link>
        </Button>
      </div>
    </header>
  );
}

function sourceOf(bank: Bank, latest: Read<LatestDecision>): string {
  if (!latest.ok) return bank === "Fed" ? "Source: Federal Reserve Board" : RBI_SOURCE;
  const m = latest.meeting;
  if (bank === "Fed") return m?.attribution ?? "Source: Federal Reserve Board";
  // RBI documents are named, never linked (Anand, 24 Oct 2026): the source line names the document, its date and
  // RBI's press-release number (Brief §6.2).
  const attribution = m?.attribution ?? RBI_SOURCE;
  return latest.document && m ? [attribution, latest.document.doc_type_label, formatDate(m.decision_date), pressReleaseLabel(latest.document)].filter(Boolean).join(" · ") : attribution;
}

function ChangesPanel({ bank, latest, changes, policies, now, footer }: { bank: Bank; latest: Read<LatestDecision>; changes: BankChanges; policies: Policies | null; now: string; footer: ReactNode }) {
  const fed = bank === "Fed";
  const m = latest.ok ? latest.meeting : null;
  const note = (kind: "error" | "unavailable", text: string) => <StateNote kind={kind}>{text}</StateNote>;
  const shell = (body: ReactNode, qualifier?: string) => (
    <Panel
      title={fed ? "FOMC statement" : "MPC resolution"}
      titleId={fed ? "panel-front-changes-fed" : "panel-front-changes-rbi"}
      qualifier={qualifier ? <span className="text-meta text-ink-3">{qualifier}</span> : undefined}
      subtitle={fed ? "What did the Fed's latest statement change, against the one before?" : "What did the RBI's latest resolution change, against the one before?"}
      market={fed ? "US" : "IN"}
      span={6}
      footer={footer}
      refresh={FEEDS.policyChanges}
    >
      {body}
    </Panel>
  );
  if (changes.kind === "error" && !m) return shell(note("error", "Couldn't load this panel. Try again."));
  if (!m || changes.kind === "no-decision") return shell(note("unavailable", "No decision is published yet."));

  const line = decisionLine(m);
  const t = latest.ok ? tally(m, latest.votes) : null;
  const head = (
    <div className="flex flex-col gap-2">
      {line ? <DecisionLine text={line} /> : note("unavailable", "The decision's rate isn't published yet.")}
      <dl className="flex flex-col gap-1 text-data">
        {m.decision_at && <Row term={fed ? "Statement" : "Resolution"}><span className="font-mono">{formatReleaseTime(m.decision_at, ZONE[bank])}</span></Row>}
        <Row term="Vote">
          {t?.kind === "tally" ? t.line : t?.kind === "pending" ? <PendingVote t={t} /> : note("unavailable", "Votes aren't published yet.")}
        </Row>
      </dl>
    </div>
  );
  const word = fed ? "statement" : "resolution";
  if (changes.kind !== "ok") {
    const text =
      changes.kind === "error"
        ? "Couldn't load this panel. Try again."
        : changes.kind === "no-document"
          ? "The decision document isn't on the site yet."
          : `There's no earlier ${word} on the site to compare this one with.`;
    return shell(<div className="flex flex-col gap-4">{head}{note(changes.kind === "error" ? "error" : "unavailable", text)}</div>);
  }
  const { model, numbers } = changes;
  return shell(
    <div className="flex flex-col gap-5">
      {head}
      {model.summary && policies ? (
        <Gated policies={policies} field="ai.statement_diff_summary" showLabel={false}>
          <AIBlock block={{ ...model.summary, label: "What changed" }} now={now} dense={false} />
        </Gated>
      ) : (
        note("unavailable", "No AI summary of the changes yet.")
      )}
      {!fed && numbers.kind !== "none" && (
        <section className="flex flex-col gap-2">
          <h3 className="text-title text-ink-1">Numbers that changed</h3>
          {numbers.kind === "error" ? (
            note("error", "Couldn't load this panel. Try again.")
          ) : numbers.kind === "unavailable" ? (
            note("unavailable", "The numbers of the two decisions can't be compared yet.")
          ) : (
            <NumbersChanged items={numbers.items} previousDate={numbers.previousDate} newDate={numbers.newDate} />
          )}
        </section>
      )}
      {model.keys ? (
        <KeyChanges model={model.keys} before={model.before} now={model.now} limit={3} seeAllHref={meetingTabHref(bank, m.decision_date, "changes")} />
      ) : (
        note("unavailable", "The redline isn't available for this document.")
      )}
    </div>,
    model.qualifier,
  );
}

export function FrontView({ front }: { front: Front }) {
  const { overview: o, fed: fedChanges, rbi: rbiChanges } = front;
  const now = o.readAt;
  const freshness = front.freshness.ok ? front.freshness.tasks : null;
  const policies = o.fieldPolicy.ok ? o.fieldPolicy.rows : null;
  const decisions = o.history.ok ? o.history.decisions : null;
  const fed = o.fed.ok ? o.fed : null;
  const rbi = o.rbi.ok ? o.rbi : null;
  const foot = (sources: (string | null | undefined)[], tasks: readonly TaskKey[], dataTo: string | null) => (
    <Foot sources={sources} tasks={tasks} dataTo={dataTo} freshness={freshness} now={now} />
  );
  const fedSource = sourceOf("Fed", o.fed);
  const rbiSource = sourceOf("RBI", o.rbi);
  const strips = o.changes.ok && o.history.ok ? buildChangeStrips(o.history.decisions, o.changes.documents, o.changes.diffs) : null;
  const changesFoot = (bank: Bank) => {
    const latest = bank === "Fed" ? o.fed : o.rbi;
    const tasks = [...new Set([...(bank === "Fed" ? FEEDS.policyLatestFed : FEEDS.policyLatestRbi), ...FEEDS.policyChanges])];
    return foot([bank === "Fed" ? fedSource : rbiSource], tasks, latest.ok ? (latest.meeting?.decision_date ?? null) : null);
  };
  const votesFoot = (bank: Bank) => {
    const latest = bank === "Fed" ? o.fed : o.rbi;
    return foot([bank === "Fed" ? fedSource : rbiSource], bank === "Fed" ? FEEDS.policyLatestFed : FEEDS.policyLatestRbi, latest.ok ? (latest.meeting?.decision_date ?? null) : null);
  };
  const votes = (bank: Bank) => {
    const latest = bank === "Fed" ? o.fed : o.rbi;
    const id = bank === "Fed" ? "panel-front-votes-fed" : "panel-front-votes-rbi";
    const title = bank === "Fed" ? "FOMC votes" : "MPC votes";
    if (!latest.ok) return <ErrorPanel title={title} id={id} span={6} market={bank === "Fed" ? "US" : "IN"} />;
    if (!latest.meeting) {
      return (
        <Panel title={title} titleId={id} span={6} market={bank === "Fed" ? "US" : "IN"} footer={votesFoot(bank)}>
          <StateNote kind="unavailable">No decision is published yet.</StateNote>
        </Panel>
      );
    }
    return (
      <VotesPanel
        bank={bank}
        m={latest.meeting}
        votes={latest.votes}
        title={title}
        titleId={id}
        qualifier={<span className="text-meta text-ink-3">{formatDate(latest.meeting.decision_date)}</span>}
        span={6}
        footer={votesFoot(bank)}
        refresh={bank === "Fed" ? FEEDS.policyLatestFed : FEEDS.policyLatestRbi}
      />
    );
  };
  return (
    <>
      <Hero year={POLICY_START.slice(0, 4)} />

      <Section id="front-what-changed" title="What changed in the latest statements" note="the bank's own words, compared sentence by sentence">
        <PanelGrid>
          <ChangesPanel bank="Fed" latest={o.fed} changes={fedChanges} policies={policies} now={now} footer={changesFoot("Fed")} />
          <ChangesPanel bank="RBI" latest={o.rbi} changes={rbiChanges} policies={policies} now={now} footer={changesFoot("RBI")} />
        </PanelGrid>
      </Section>

      <Section id="front-votes" title="How the members voted" note="one dot per member, at the change they voted for">
        <PanelGrid>
          {votes("Fed")}
          {votes("RBI")}
        </PanelGrid>
      </Section>

      <Section id="front-change-strip" title="How much each statement changed">
        <PanelGrid>
          {strips ? (
            <ChangeStrip
              strips={strips}
              scale={stripScale(strips)}
              refresh={FEEDS.policyChanges}
              footer={foot([fedSource, rbiSource], FEEDS.policyChanges, latestOf(...strips.flatMap((s) => s.bars.map((b) => b.date))))}
            />
          ) : (
            <ErrorPanel title="How much each statement changed" id="panel-change-strip" refresh={FEEDS.policyChanges} />
          )}
        </PanelGrid>
      </Section>

      <Section id="front-where-rates-stand" title="Where rates stand">
        <PanelGrid>
          <Panel
            label="Where the two policy rates stand now, and the next meetings"
            variant="flush"
            footer={
              <>
                {foot([fedSource, rbiSource], FEEDS.policyRatesStand, latestOf(fed?.meeting?.decision_date, rbi?.meeting?.decision_date))}
                <Link href="/policy" className="text-ink-1 underline">
                  Open the Fed vs RBI tracker →
                </Link>
              </>
            }
          >
            <RateStats fed={fed} rbi={rbi} decisions={decisions} policies={policies} meetings={front.next.ok ? front.next.meetings : null} />
          </Panel>
        </PanelGrid>
      </Section>
    </>
  );
}
