// The front page (/, #152) rendered to HTML as the server sends it: its four sections in order, both banks, one bank's
// failed read leaving the other whole, every AI line cited, the RBI's documents named and never linked, and no panel
// blank. Test rows, not real decisions.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FrontView } from "@/components/policy/FrontView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { changesModel, numbersModel } from "@/lib/policy/changes";
import type { AiCitationRow, AiOutputRow, DocumentDiffRow, DocumentRow, FieldPolicyRow, PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import type { BankChanges, Front } from "@/lib/queries/front";
import type { LatestDecision, PolicyOverview } from "@/lib/queries/overview";

const html = (node: ReactNode) => renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const NOW = "2026-10-24T03:00:00Z";

const POLICIES: FieldPolicyRow[] = [
  { field_key: "ai.doc_summary", status: "labelled", label: "AI summary" },
  { field_key: "ai.statement_diff_summary", status: "labelled", label: "AI summary" },
  { field_key: "ai.stance_extraction", status: "labelled", label: "AI-extracted" },
];

const FED: PolicyMeetingRow = {
  slug: "fomc-2026-09-17", bank: "Fed", status: "held", decision_date: "2026-09-17", decision_at: "2026-09-17T18:00:00+00:00",
  kind: "scheduled", is_rate_decision: true, meeting_no: null, minutes_due_on: "2026-10-08", policy_rate_pct: 3.5,
  policy_rate_change_bps: -25, stance: null, stance_display_label: null, dissent_count: 1, attribution: "Source: Federal Reserve Board",
  fed_target_lower_pct: 3.25, fed_target_upper_pct: 3.5,
};
const RBI: PolicyMeetingRow = {
  ...FED, slug: "mpc-2026-10-07", bank: "RBI", decision_date: "2026-10-07", decision_at: "2026-10-07T04:30:00+00:00", meeting_no: 61,
  minutes_due_on: "2026-10-21", policy_rate_pct: 5.5, policy_rate_change_bps: 25, stance: "calibrated tightening",
  stance_display_label: "AI-extracted", dissent_count: 0, attribution: "Source: Reserve Bank of India", fed_target_lower_pct: null, fed_target_upper_pct: null,
};
const vote = (slug: string, name: string, bps: number, dissent = false): PolicyVoteRow => ({
  meeting_slug: slug, member_slug: name, member_name: name, voted_change_bps: bps, stance_vote: null, is_dissent: dissent, dissent_text: null,
});
const doc = (id: number, bank: "Fed" | "RBI", date: string, slug: string, url: string): DocumentRow => ({
  id, doc_type: bank === "Fed" ? "fomc_statement" : "mpc_resolution", doc_type_label: bank === "Fed" ? "FOMC statement" : "MPC resolution",
  entity_slug: bank.toLowerCase(), event_slug: slug, title: "Decision document", source_url: url,
  attribution: bank === "Fed" ? "Source: Federal Reserve Board" : "Source: Reserve Bank of India", published_at: `${date}T12:00:00+00:00`, version_no: 1,
});
const FED_DOC = doc(2, "Fed", "2026-09-17", FED.slug, "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260917a.htm");
const FED_BASE = doc(1, "Fed", "2026-07-29", "fomc-2026-07-29", "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm");
const RBI_DOC = doc(4, "RBI", "2026-10-07", RBI.slug, "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63742");
const RBI_BASE = doc(3, "RBI", "2026-08-06", "mpc-2026-08-06", "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63000");

const diff = (id: number, base: number, newSentence: string): DocumentDiffRow => ({
  document_id: id, base_document_id: base, words_added: 5, words_removed: 3,
  segments: {
    version: 2,
    pairs: [
      { kind: "same", topic: "Inflation", text: "Unchanged.", old_para: [1], new_para: [1] },
      { kind: "changed", topic: "Decision and guidance", old_para: [2], new_para: [2], ops: [{ op: "eq", text: "Decided to " }, { op: "del", text: "hold" }, { op: "ins", text: "cut" }, { op: "eq", text: "." }] },
      { kind: "added", topic: "Monitoring", text: newSentence, old_para: [], new_para: [3] },
      { kind: "added", topic: "Votes", text: "Voting for the action were A and B.", old_para: [], new_para: [4] },
      { kind: "removed", topic: "Growth", text: "A removed growth sentence.", old_para: [5], new_para: [] },
      { kind: "added", topic: "Other", text: "A fifth changed sentence.", old_para: [], new_para: [6] },
    ],
  },
});
const summary = (id: number, docId: number, text: string) => ({
  output: { id, task: "statement_diff_summary", document_id: docId, event_slug: null, scope: "", body: "", output_label: null, display_label: "What changed", model: "claude-haiku-5-5", created_at: "2026-09-17T18:31:00+00:00", content: { bullets: [{ text, citations: [1] }] } } as AiOutputRow,
  citations: [{ ai_output_id: id, citation_no: 1, claim_path: "/bullets/0", document_id: docId, document_title: "Decision document", source_url: "https://www.federalreserve.gov/x.htm", char_start: 10, char_end: 60, quote: "decided to lower the target range" }] as AiCitationRow[],
});

const fedLatest: LatestDecision = {
  meeting: FED, votes: [...Array.from({ length: 11 }, (_, i) => vote(FED.slug, `Member ${i + 1}`, -25)), vote(FED.slug, "Miran", -50, true)],
  document: FED_DOC, summary: null, stance: null,
};
const rbiLatest: LatestDecision = { meeting: RBI, votes: [], document: RBI_DOC, summary: null, stance: null };

const fedChanges: BankChanges = {
  kind: "ok",
  model: changesModel({ bank: "Fed", m: FED, decisionDoc: FED_DOC, diff: diff(2, 1, "Fed monitoring sentence."), base: FED_BASE, summary: summary(11, 2, "The Committee moved from hold to cut.") }),
  numbers: { kind: "none" },
};
const rbiChanges: BankChanges = {
  kind: "ok",
  model: changesModel({ bank: "RBI", m: RBI, decisionDoc: RBI_DOC, diff: diff(4, 3, "RBI monitoring sentence."), base: RBI_BASE, summary: summary(12, 4, "The MPC raised the repo rate.") }),
  numbers: numbersModel({
    m: RBI,
    base: { ...RBI_BASE, event_slug: "mpc-2026-08-06" },
    current: { settings: [{ meeting_slug: RBI.slug, bank: "RBI", decision_date: RBI.decision_date, instrument: "repo", value_pct: 5.5, change_bps: 25 } as never], votes: [], projections: [] },
    earlier: { meeting: { ...RBI, slug: "mpc-2026-08-06", decision_date: "2026-08-06", policy_rate_pct: 5.25 }, settings: [{ meeting_slug: "mpc-2026-08-06", bank: "RBI", decision_date: "2026-08-06", instrument: "repo", value_pct: 5.25, change_bps: 0 } as never], votes: [], projections: [] },
    stance: { show: true, label: "AI-extracted" },
  }),
};

const no = { ok: false } as const;
const overview = (over: Partial<PolicyOverview> = {}): PolicyOverview => ({
  readAt: NOW,
  fieldPolicy: { ok: true, rows: POLICIES },
  history: { ok: true, decisions: [FED, RBI], votes: fedLatest.votes },
  fed: { ok: true, ...fedLatest },
  rbi: { ok: true, ...rbiLatest },
  rates: no, reactions: no, longRates: no,
  changes: { ok: true, documents: [{ id: 1, event_slug: "fomc-2026-07-29", version_no: 1 }, { id: 2, event_slug: FED.slug, version_no: 1 }], diffs: [] },
  ...over,
});
const front = (over: Partial<Front> = {}, o: Partial<PolicyOverview> = {}): Front => ({
  overview: overview(o),
  freshness: { ok: true, tasks: [], readAt: NOW },
  next: { ok: true, meetings: [{ bank: "Fed", slug: "fomc-2026-10-28", decision_date: "2026-10-28", decision_at: "2026-10-28T18:00:00+00:00" }] },
  fed: fedChanges,
  rbi: rbiChanges,
  ...over,
});

describe("the front page (/)", () => {
  const out = html(<FrontView front={front()} />);
  const t = text(out);

  it("leads with the positioning, the way the site works and the two buttons", () => {
    expect(out).toMatch(/<h1[^>]*>An AI research terminal for Indian markets and the Fed–RBI policy cycle\.<\/h1>/);
    expect(t).toContain("Start here");
    expect(t).toContain("Every FOMC and RBI decision since 2019");
    expect(t).toContain("An AI line that can't be cited isn't shown");
    expect(t).toContain("Every figure names its source and date");
    expect(t).toContain("End-of-day data, checked daily");
    expect(out).toContain('href="/policy"');
    expect(out).toContain('href="/policy#history"');
    expect(t).toContain("Open the Fed vs RBI tracker");
    expect(t).toContain("Every decision since 2019");
  });

  it("has its four sections in this order", () => {
    const heads = [...out.matchAll(/<h2[^>]*>(.*?)<\/h2>/g)].map((m) => text(m[1]).replace(/ the bank's own words.*| one dot per member.*/, ""));
    expect(heads).toEqual(["What changed in the latest statements", "How the members voted", "How much each statement changed", "Where rates stand"]);
  });

  it("shows each bank's What changed: decision, tally, release time, cited summary, three Key changes rows and the link to the rest", () => {
    expect(t).toContain("FOMC statement 17 Sep 2026 vs 29 Jul 2026");
    expect(t).toContain("MPC resolution 7 Oct 2026 vs 6 Aug 2026");
    expect(t).toContain("Cut 25 bps to 3.25–3.50%");
    expect(t).toContain("Hike 25 bps to 5.50%");
    expect(t).toContain("Statement 14:00 ET (23:30 IST)");
    expect(t).toMatch(/What changed .*The Committee moved from hold to cut\. \[1\]/);
    expect(t).toMatch(/What changed .*The MPC raised the repo rate\. \[1\]/);
    expect(t).toContain("Numbers that changed");
    // three rows, in Key changes' own order (decision first), then the others
    expect(t).toContain("Fed monitoring sentence.");
    expect(t).not.toContain("A fifth changed sentence.");
    expect(t.match(/2 other sentences changed · See all changes →/g)).toHaveLength(2);
    expect(out).toContain('href="/policy/fed/2026-09-17?tab=changes"');
    expect(out).toContain('href="/policy/rbi/2026-10-07?tab=changes"');
    expect(out).toContain("border-t-series-1");
    expect(out).toContain("border-t-series-2");
  });

  it("shows the Fed's votes and the RBI's honest pending note, not an older meeting", () => {
    expect(t).toContain("Vote 11–1 · Dissent: Miran (preferred −50 bps)");
    expect(t).toContain("Each member's vote is published in the minutes, due 21 Oct 2026.");
  });

  it("puts where rates stand last, ending in the way into the tracker", () => {
    expect(t).toMatch(/Fed funds target range .*3\.25–3\.50.*RBI repo rate .*5\.50/);
    expect(t).toContain("Next FOMC 28 Oct 2026");
    expect(t).toMatch(/Open the Fed vs RBI tracker →\s*$/);
  });

  it("never links the RBI's pages, shows no hawk/dove and no verdict", () => {
    expect(out).not.toContain("rbi.org.in");
    expect(t).not.toMatch(/hawk|dove|buy|sell|upside/i);
    expect(t).toContain("Source: Reserve Bank of India · MPC resolution · 7 Oct 2026");
  });

  it("is marked informational: no panel is blank and every panel has a source line", () => {
    expect((out.match(/<section[^>]*aria-labelledby="panel-/g) ?? []).length).toBeGreaterThanOrEqual(4);
    expect(t).toContain("Source: Federal Reserve Board");
  });
});

describe("the front page between a tally statement and its minutes (#159)", () => {
  const OCT: PolicyMeetingRow = {
    ...FED, slug: "fomc-2026-10-28", decision_date: "2026-10-28", decision_at: "2026-10-28T18:00:00+00:00", minutes_due_on: "2026-11-18",
    policy_rate_pct: 3.5, policy_rate_change_bps: 0, dissent_count: 2,
  };
  const NOTE = "Each member's vote is published in the minutes, due 18 Nov 2026.";
  const latest = (votes: PolicyVoteRow[], meeting: PolicyMeetingRow = OCT): Partial<PolicyOverview> => ({
    fed: { ok: true, ...fedLatest, meeting, votes },
    history: { ok: true, decisions: [meeting, RBI], votes },
  });
  const page = (votes: PolicyVoteRow[], meeting?: PolicyMeetingRow) => {
    const out = html(<FrontView front={front({}, latest(votes, meeting))} />);
    return { out, t: text(out) };
  };

  it("shows the dissenters and the minutes copy in both the What changed and the votes panels, with no count", () => {
    const { out, t } = page([vote(OCT.slug, "Lisa D. Cook", 25, true), vote(OCT.slug, "Beth M. Hammack", 25, true)]);
    const line = `Dissent: Lisa D. Cook (preferred +25 bps), Beth M. Hammack (preferred +25 bps) · ${NOTE}`;
    expect(t.split(line)).toHaveLength(3); // twice: the statement panel's Vote row, and "How the members voted"
    expect(t).not.toContain("Votes aren't published yet.");
    expect(t).not.toMatch(/Vote \d+–\d+/);
    expect(out).not.toContain("hollow dot"); // no dot plot for a partial meeting
  });

  it("shows only the copy when the statement named nobody", () => {
    const { t } = page([]);
    expect(t.split(NOTE)).toHaveLength(3);
    expect(t).not.toContain("Dissent:");
    expect(t).not.toContain("Votes aren't published yet.");
  });

  it("is the tally line again, with the dot plot, once the minutes add the members who voted for", () => {
    const full = [...Array.from({ length: 10 }, (_, i) => vote(OCT.slug, `Member ${i + 1}`, 0)), vote(OCT.slug, "Lisa D. Cook", 25, true), vote(OCT.slug, "Beth M. Hammack", 25, true)];
    const { t } = page(full);
    expect(t).toContain("Vote 10–2 · Dissent: Lisa D. Cook (preferred +25 bps), Beth M. Hammack (preferred +25 bps)");
    expect(t).not.toContain(NOTE); // the minutes are in: nothing left to wait for
  });
});

describe("the front page when a read fails", () => {
  it("leaves the other bank's What changed whole when one bank's part failed", () => {
    const t = text(html(<FrontView front={front({ fed: { kind: "error" } })} />));
    expect(t).toContain("Couldn't load this panel. Try again.");
    expect(t).toContain("The MPC raised the repo rate. [1]");
    expect(t).toContain("Cut 25 bps to 3.25–3.50%"); // the Fed's decision (from the overview) still shows
  });

  it("says why when there is no earlier document to compare, or no document yet", () => {
    const a = text(html(<FrontView front={front({ rbi: { kind: "no-earlier" } })} />));
    expect(a).toContain("There's no earlier resolution on the site to compare this one with.");
    const b = text(html(<FrontView front={front({ fed: { kind: "no-document" } })} />));
    expect(b).toContain("The decision document isn't on the site yet.");
  });

  it("shows no-decision and a failed overview as states, never blank, and the rest of the page still renders", () => {
    const t = text(html(<FrontView front={front({ fed: { kind: "no-decision" } }, { fed: no, history: no, changes: no, fieldPolicy: no })} />));
    expect(t).toContain("No decision is published yet.");
    expect(t).toContain("Couldn't load this panel. Try again.");
    expect(t).toContain("Where rates stand");
    expect(t).toContain("Couldn't load this rate.");
  });

  it("hides the AI summary when its field policy is missing (fail closed), keeping the sentences", () => {
    const t = text(html(<FrontView front={front({}, { fieldPolicy: { ok: true, rows: [] } })} />));
    expect(t).not.toContain("The Committee moved from hold to cut.");
    expect(t).toContain("Fed monitoring sentence.");
  });
});
