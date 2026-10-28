// /policy's panels rendered to HTML as the server sends them (C3): every state says something (no blank panel),
// the RBI's documents are never linked (Anand, 24 Oct 2026), gated fields follow api.field_policy, and the AI text
// shows only with its citations. Test rows, not real decisions.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Gated } from "@/components/data/Gated";
import { HistoryPanel } from "@/components/policy/HistoryPanel";
import { PolicyCard } from "@/components/policy/PolicyCard";
import { LongRatesPanel } from "@/components/policy/LongRatesPanel";
import { ReactionsPanel } from "@/components/policy/ReactionsPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { historyRows } from "@/lib/policy/decisions";
import { REACTION_SERIES, reactionRows } from "@/lib/policy/reactions";
import type { AiCitationRow, AiOutputRow, FieldPolicyRow, ObservationRow, PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import type { LatestDecision } from "@/lib/queries/overview";

const html = (node: ReactNode) => renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const NOW = "2026-10-24T03:00:00Z";

const POLICIES: FieldPolicyRow[] = [
  { field_key: "ai.doc_summary", status: "labelled", label: "AI summary" },
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
const summary = (id: number, url: string): { output: AiOutputRow; citations: AiCitationRow[] } => ({
  output: {
    id, task: "doc_summary", document_id: 5, event_slug: null, scope: "", body: "", output_label: null, display_label: "AI summary",
    model: "claude-haiku-5-5", created_at: "2026-09-17T18:31:00+00:00",
    content: { bullets: [{ text: "The Committee lowered the target range by a quarter point.", citations: [1] }] },
  },
  citations: [{ ai_output_id: id, citation_no: 1, claim_path: "/bullets/0", document_id: 5, document_title: "Decision document", source_url: url, char_start: 10, char_end: 60, quote: "decided to lower the target range" }],
});

const fedLatest: LatestDecision = {
  meeting: FED,
  votes: [...Array.from({ length: 11 }, (_, i) => vote(FED.slug, `Member ${i + 1}`, -25)), vote(FED.slug, "Miran", -50, true)],
  document: { id: 5, doc_type: "fomc_statement", doc_type_label: "FOMC statement", entity_slug: "fed", event_slug: FED.slug, title: "FOMC statement", source_url: "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260917a.htm", attribution: "Source: Federal Reserve Board", published_at: FED.decision_at, version_no: 1 },
  summary: summary(1, "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260917a.htm"),
  stance: null,
};
const rbiLatest: LatestDecision = {
  meeting: RBI,
  votes: [],
  document: { id: 6, doc_type: "mpc_resolution", doc_type_label: "MPC resolution", entity_slug: "rbi", event_slug: RBI.slug, title: "Resolution of the MPC", source_url: "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63742", attribution: "Source: Reserve Bank of India", published_at: RBI.decision_at, version_no: 1 },
  summary: summary(2, "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63742"),
  stance: null,
};

describe("PolicyCard", () => {
  it("shows the Fed's decision, release time, tally, cited summary and its links", () => {
    const out = html(<PolicyCard bank="Fed" latest={fedLatest} policies={POLICIES} now={NOW} footer="Source: Federal Reserve Board" />);
    const t = text(out);
    expect(t).toContain("Latest Fed decision 17 Sep 2026");
    expect(t).toContain("Cut 25 bps to 3.25–3.50%");
    expect(t).toContain("Statement 14:00 ET (23:30 IST)");
    expect(t).toContain("Vote 11–1 · Dissent: Miran (preferred −50 bps)");
    expect(t).toMatch(/AI summary .*The Committee lowered the target range by a quarter point\. \[1\]/);
    expect(t.match(/AI summary/g)).toHaveLength(1); // the block's header names it; no second tag
    expect(out).toContain('href="/policy/fed/2026-09-17"');
    expect(out).toContain('href="https://www.federalreserve.gov/newsevents/pressreleases/monetary20260917a.htm"');
    expect(out).toContain("border-t-series-1"); // the US market edge
    expect(t).not.toMatch(/hawk|dove/i); // D9
  });

  it("never links the RBI's pages, labels the stance and waits for the minutes' votes", () => {
    const out = html(<PolicyCard bank="RBI" latest={rbiLatest} policies={POLICIES} now={NOW} footer="Source: Reserve Bank of India · MPC resolution · 7 Oct 2026" />);
    const t = text(out);
    expect(t).toContain("Hike 25 bps to 5.50%");
    expect(t).toContain("Resolution 10:00 IST");
    expect(t).toContain("Stance Calibrated tightening AI-extracted");
    expect(t).toContain("Each member's vote is published in the minutes, due 21 Oct 2026.");
    expect(t).toContain("MPC meeting no. 61, 7 Oct 2026");
    expect(out).not.toContain("rbi.org.in");
    expect(t).not.toContain("Official");
    expect(out).toContain("border-t-series-2");
  });

  it("links the meeting, then its What changed tab, then (Fed only) the official statement (#147)", () => {
    const row = (out: string) => {
      const p = /<p class="mt-3 flex[^"]*">([\s\S]*?)<\/p>/.exec(out);
      expect(p).not.toBeNull();
      // next/link writes `class` before `href`, so the attributes are read in any order
      return [...p![1].matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)].map((m) => [/href="([^"]*)"/.exec(m[1])![1], text(m[2])]);
    };
    const fed = html(<PolicyCard bank="Fed" latest={fedLatest} policies={POLICIES} now={NOW} footer="" />);
    expect(row(fed)).toEqual([
      ["/policy/fed/2026-09-17", "FOMC meeting, 17 Sep 2026"],
      ["/policy/fed/2026-09-17?tab=changes", "What changed"],
      ["https://www.federalreserve.gov/newsevents/pressreleases/monetary20260917a.htm", "Official statement ↗"],
    ]);
    const rbi = html(<PolicyCard bank="RBI" latest={rbiLatest} policies={POLICIES} now={NOW} footer="" />);
    expect(row(rbi)).toEqual([
      ["/policy/rbi/2026-10-07", "MPC meeting no. 61, 7 Oct 2026"],
      ["/policy/rbi/2026-10-07?tab=changes", "What changed"],
    ]);
  });

  it("hides the stance and the summary when their field policy is hidden or missing (fail closed)", () => {
    const t = text(html(<PolicyCard bank="RBI" latest={rbiLatest} policies={[]} now={NOW} footer="" />));
    expect(t).not.toContain("Calibrated tightening");
    expect(t).not.toContain("The Committee lowered");
  });

  it("is never blank: no decision yet, or a failed read", () => {
    expect(text(html(<PolicyCard bank="Fed" latest={{ ...fedLatest, meeting: null }} policies={POLICIES} now={NOW} footer="" />))).toContain("No decision is published yet.");
    expect(text(html(<PolicyCard bank="Fed" latest={null} policies={POLICIES} now={NOW} footer="" />))).toContain("Couldn't load this panel. Try again.");
    expect(text(html(<PolicyCard bank="Fed" latest={{ ...fedLatest, summary: null }} policies={POLICIES} now={NOW} footer="" />))).toContain("No AI summary of this decision yet.");
  });
});

describe("HistoryPanel", () => {
  it("lists every decision with its filters, linking each row to its meeting page", () => {
    const rows = historyRows([FED, RBI], fedLatest.votes);
    const out = html(<HistoryPanel rows={rows} stanceLabel="AI-extracted" footer="Sources" />);
    const t = text(out);
    expect(t).toContain("Every decision since 2019");
    expect(t).toContain("2 decisions");
    expect(t).toContain("Stance, RBI (AI-extracted)");
    expect(t).toMatch(/07 Oct 2026 RBI Scheduled Hike 5\.50 \+25 — 0 Calibrated tightening/);
    expect(t).toMatch(/17 Sep 2026 Fed Scheduled Cut 3\.25–3\.50 −25 11–1 1/);
    expect(out).toContain('href="/policy/rbi/2026-10-07"');
    expect(out).toContain('aria-sort="descending"');
    // the filters stay above a capped table; its other rows scroll inside the panel (Anand, 28 Oct 2026)
    expect(out).toMatch(/role="region" aria-label="Every FOMC and MPC rate decision since 2019" tabindex="0"/);
    expect(out.indexOf("2 decisions")).toBeLessThan(out.indexOf('role="region"'));
  });

  it("says so when nothing matches", () => {
    expect(text(html(<HistoryPanel rows={[]} stanceLabel={null} footer="" />))).toContain("No decisions match these filters.");
  });
});

describe("ReactionsPanel (Pass 1b, #70)", () => {
  const series = REACTION_SERIES.map((s) => ({ key: s.key, name: s.key, market: null, attribution: null }));
  const move = (series_key: string, change: number, unit: "bps" | "pct") => ({
    meeting_slug: FED.slug, series_key, series_name: series_key, pre_obs_date: "2026-09-16", post_obs_date: "2026-09-17",
    pre_value: 3.61, post_value: 3.52, change, change_unit: unit, is_surprise_measure: series_key === "fred.DGS2", attribution: null,
  });

  it("shows each decision's moves as signed deltas, says they're derived, and waits for the rest", () => {
    const rows = reactionRows(historyRows([FED, RBI], []), [move("fred.DGS2", -9, "bps"), move("yf.^GSPC", 0.4213, "pct")], series);
    const out = html(<ReactionsPanel rows={rows} footer="Sources" />);
    const t = text(out);
    expect(t).toContain("Decision-day moves");
    expect(t).toContain("How did markets react to each decision?");
    expect(t).toContain("Derived:");
    expect(t).toContain("US 2Y (bps)");
    expect(t).toContain("down 9");
    expect(t).toContain("up 0.42");
    expect(out).toContain("text-down");
    expect(out).toContain("text-up");
    expect(t).toContain("Waiting for the first close after the decision.");
    expect(t).toContain("2 decisions");
    expect(out).toContain('href="/policy/fed/2026-09-17"');
    expect(out).toMatch(/role="region" aria-label="Market moves around each FOMC and MPC rate decision since 2019" tabindex="0"/);
  });

  it("is never blank when no decision is listed", () => {
    expect(text(html(<ReactionsPanel rows={[]} footer="" />))).toContain("No decisions match this filter.");
  });
});

describe("LongRatesPanel (Pass 1b, #71)", () => {
  const o = (series_key: string, obs_date: string, value: number): ObservationRow => ({ series_key, obs_date, value, label: null });
  const us = [o("fred.DGS10", "2026-10-21", 4.08), o("fred.DGS10", "2026-10-22", 4.11)];
  const india = [o("fred.INDIRLTLT01STM", "2026-09-01", 6.41)];

  it("answers its question, labels India's yield monthly and carries the latest values", () => {
    const out = text(html(<LongRatesPanel us={us} india={india} from="2019-01-01" footer="Source line" />));
    expect(out).toContain("Long rates: US vs India 10-year");
    expect(out).toContain("How have long-term yields moved in each market?");
    expect(out).toContain("4.11%");
    expect(out).toContain("6.41%");
    expect(out).toContain("monthly, Sep 2026");
    expect(out).toContain("Source line");
  });

  it("offers the table view", () => {
    expect(html(<LongRatesPanel us={us} india={india} from="2019-01-01" footer="" />)).toContain('aria-label="View as table"');
  });

  it("says so, never a blank panel, when no yields are published", () => {
    const out = text(html(<LongRatesPanel us={[]} india={[]} from="2019-01-01" footer="" />));
    expect(out).toContain("No long-term yields are published yet.");
  });
});

describe("Gated", () => {
  it("shows, labels or hides a field as api.field_policy says (no row: hidden)", () => {
    const g = (status: FieldPolicyRow["status"] | null) =>
      text(html(<Gated policies={status ? [{ field_key: "f", status, label: "monthly" }] : []} field="f">value</Gated>));
    expect(g("shown")).toBe("value");
    expect(g("labelled")).toBe("value monthly");
    expect(g("hidden")).toBe("");
    expect(g(null)).toBe("");
  });
});
