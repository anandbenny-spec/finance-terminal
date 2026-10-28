// What the policy pages say about a decision (C3): DecisionLine, the tally lines (F23) and the history rows.
// Test rows shaped like the views; the names and figures are test data, not a record of real meetings.
import { describe, expect, it } from "vitest";
import type { PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import { TABS, changeInWords, decisionLine, historyRows, lastMove, meetingHref, meetingTabHref, tabOf, tally } from "./decisions";

function meeting(over: Partial<PolicyMeetingRow>): PolicyMeetingRow {
  return {
    slug: "fomc-2026-09-17", bank: "Fed", status: "held", decision_date: "2026-09-17", decision_at: "2026-09-17T18:00:00+00:00",
    kind: "scheduled", is_rate_decision: true, meeting_no: null, minutes_due_on: "2026-10-08", policy_rate_pct: 3.5,
    policy_rate_change_bps: -25, stance: null, stance_display_label: null, dissent_count: 0,
    attribution: "Source: Federal Reserve Board", fed_target_lower_pct: 3.25, fed_target_upper_pct: 3.5, ...over,
  };
}

function vote(slug: string, name: string, bps: number | null, over: Partial<PolicyVoteRow> = {}): PolicyVoteRow {
  return { meeting_slug: slug, member_slug: name.toLowerCase(), member_name: name, voted_change_bps: bps, stance_vote: null, is_dissent: false, dissent_text: null, ...over };
}

const rbi = (over: Partial<PolicyMeetingRow>) =>
  meeting({ slug: "mpc-2024-08-08", bank: "RBI", decision_date: "2024-08-08", decision_at: "2024-08-08T04:30:00+00:00", meeting_no: 50, minutes_due_on: "2024-08-22", policy_rate_pct: 6.5, policy_rate_change_bps: 0, fed_target_lower_pct: null, fed_target_upper_pct: null, attribution: "Source: Reserve Bank of India", ...over });

describe("decisionLine (Design 01 §4.5): words and numbers only", () => {
  it("writes a cut, a hike and a hold", () => {
    expect(decisionLine(meeting({}))).toBe("Cut 25 bps to 3.25–3.50%");
    expect(decisionLine(meeting({ policy_rate_change_bps: 50, fed_target_lower_pct: 5.25, fed_target_upper_pct: 5.5 }))).toBe("Hike 50 bps to 5.25–5.50%");
    expect(decisionLine(meeting({ policy_rate_change_bps: 0 }))).toBe("Held at 3.25–3.50%");
    expect(decisionLine(rbi({ policy_rate_change_bps: -35, policy_rate_pct: 5.4 }))).toBe("Cut 35 bps to 5.40%");
  });

  it("says nothing when the rate or its change is missing (absent, not guessed)", () => {
    expect(decisionLine(meeting({ policy_rate_change_bps: null }))).toBeNull();
    expect(decisionLine(meeting({ fed_target_lower_pct: null }))).toBeNull();
  });

  it("writes a change with its date, for a stat", () => {
    expect(changeInWords(meeting({}))).toBe("Cut 25 bps on 17 Sep 2026");
    expect(changeInWords(rbi({}))).toBe("Held on 8 Aug 2024");
  });

  it("finds the last decision that moved the rate", () => {
    const a = meeting({ slug: "a", policy_rate_change_bps: -25 });
    const b = meeting({ slug: "b", policy_rate_change_bps: 0 });
    expect(lastMove([a, b])?.slug).toBe("a");
    expect(lastMove([b])).toBeNull();
  });

  it("links to the meeting page by bank and decision date", () => {
    expect(meetingHref("Fed", "2026-09-17")).toBe("/policy/fed/2026-09-17");
    expect(meetingHref("RBI", "2026-10-07")).toBe("/policy/rbi/2026-10-07");
  });
});

describe("meeting page tabs (moved from MeetingView, #147)", () => {
  it("lists the tabs in the page's order", () => {
    expect(TABS.map((t) => t.id)).toEqual(["overview", "changes", "statement", "minutes", "press", "reaction"]);
    expect(TABS.find((t) => t.id === "changes")?.label).toBe("What changed");
  });

  it("reads ?tab= as the tab it names, and anything else as the Overview", () => {
    expect(tabOf("changes")).toBe("changes");
    expect(tabOf(["reaction", "changes"])).toBe("reaction"); // the first of a repeated parameter
    expect(tabOf("nonsense")).toBe("overview");
    expect(tabOf("")).toBe("overview");
    expect(tabOf(undefined)).toBe("overview");
    expect(tabOf([])).toBe("overview");
  });
});

describe("meetingTabHref: a meeting page on a given tab", () => {
  it("is the bare meeting path for the Overview, and ?tab= for every other tab", () => {
    expect(meetingTabHref("Fed", "2026-09-16", "overview")).toBe("/policy/fed/2026-09-16");
    expect(meetingTabHref("Fed", "2026-09-16", "changes")).toBe("/policy/fed/2026-09-16?tab=changes");
    expect(meetingTabHref("RBI", "2026-10-07", "statement")).toBe("/policy/rbi/2026-10-07?tab=statement");
    for (const t of TABS) {
      expect(tabOf(new URL(meetingTabHref("RBI", "2026-10-07", t.id), "https://x.test").searchParams.get("tab") ?? undefined)).toBe(t.id);
    }
  });

  it("adds one more query parameter after the tab, or alone on the Overview", () => {
    expect(meetingTabHref("RBI", "2026-10-07", "statement", "doc=governor")).toBe("/policy/rbi/2026-10-07?tab=statement&doc=governor");
    expect(meetingTabHref("RBI", "2026-10-07", "overview", "doc=governor")).toBe("/policy/rbi/2026-10-07?doc=governor");
  });
});

describe("Fed tally (Design 01 §1.14, §4.5)", () => {
  const slug = "fomc-2026-09-17";
  const eleven = Array.from({ length: 11 }, (_, i) => vote(slug, `Member ${String.fromCharCode(65 + i)}`, -25));

  it("reads 'Vote 11–1 · Dissent: name (preferred −50 bps)'", () => {
    const t = tally(meeting({}), [...eleven, vote(slug, "Miran", -50, { is_dissent: true })]);
    expect(t).toEqual({ kind: "tally", line: "Vote 11–1 · Dissent: Miran (preferred −50 bps)", short: "11–1", dissents: ["Miran (preferred −50 bps)"] });
  });

  it("counts a dissent that isn't about the rate (Sep 2020's kind) without inventing a preference", () => {
    const t = tally(meeting({}), [...eleven.slice(0, 8), vote(slug, "Kaplan", null, { is_dissent: true }), vote(slug, "Kashkari", null, { is_dissent: true })]);
    expect(t.kind === "tally" && t.line).toBe("Vote 8–2 · Dissent: Kaplan (dissented, not on the rate), Kashkari (dissented, not on the rate)");
  });

  it("writes a hold preference in words", () => {
    const t = tally(meeting({}), [...eleven, vote(slug, "Schmid", 0, { is_dissent: true })]);
    expect(t.kind === "tally" && t.line).toBe("Vote 11–1 · Dissent: Schmid (preferred to hold)");
  });

  it("is absent with no votes, and counts only the meeting's own rows", () => {
    expect(tally(meeting({}), [])).toEqual({ kind: "absent" });
    expect(tally(meeting({}), [vote("fomc-2026-07-30", "A", 0)])).toEqual({ kind: "absent" });
  });
});

describe("RBI tally (F23): rate and stance counted separately", () => {
  const slug = "mpc-2024-08-08";
  const names = ["Das", "Patra", "Ranjan", "Bhide", "Goyal", "Varma"];

  it("reads 'Rate 4–2' with both dissenters' preference when the stance votes aren't all stored", () => {
    const votes = names.map((n, i) => vote(slug, n, i >= 4 ? -25 : 0, { is_dissent: i >= 4 }));
    const t = tally(rbi({}), votes);
    expect(t.kind === "tally" && t.line).toBe("Rate 4–2 · Dissent: Goyal (preferred −25 bps), Varma (preferred −25 bps)");
    expect(t.kind === "tally" && t.short).toBe("4–2");
  });

  it("reads 'Rate 6–0 · Stance 4–2' and names the stance dissenters", () => {
    const m = rbi({ slug: "mpc-2026-10-07", decision_date: "2026-10-07", policy_rate_change_bps: 25, policy_rate_pct: 5.5 });
    const votes = names.map((n, i) =>
      vote(m.slug, n, 25, { stance_vote: i < 4 ? "calibrated tightening" : "neutral", is_dissent: i >= 4 }),
    );
    const t = tally(m, votes);
    expect(t.kind === "tally" && t.line).toBe(
      "Rate 6–0 · Stance 4–2 · Dissent: Goyal (stance: voted neutral), Varma (stance: voted neutral)",
    );
    expect(t.kind === "tally" && t.short).toBe("6–0 · stance 4–2");
  });

  it("names a stored stance dissent ('against') even when the stance tally can't be counted", () => {
    const votes = names.map((n, i) => vote(slug, n, 0, i === 5 ? { stance_vote: "against", is_dissent: true } : {}));
    expect(tally(rbi({}), votes)).toMatchObject({ kind: "tally", line: "Rate 6–0 · Dissent: Varma (voted against the stance)" });
  });

  it("shows no stance tally without a strict majority (a 3–3 split)", () => {
    const votes = names.map((n, i) => vote(slug, n, 0, { stance_vote: i < 3 ? "neutral" : "accommodative", is_dissent: i >= 3 }));
    const t = tally(rbi({}), votes);
    expect(t.kind === "tally" && t.line.startsWith("Rate 6–0 · Dissent:")).toBe(true);
  });

  it("waits for the minutes (§4.5 copy) when not every member's vote is stored (D4)", () => {
    expect(tally(rbi({}), [vote(slug, "Das", 0)])).toEqual({
      kind: "pending",
      note: "Each member's vote is published in the minutes, due 22 Aug 2024.",
    });
  });
});

describe("historyRows", () => {
  it("lists decisions newest first, with formatted cells and the RBI's stance only", () => {
    const f = meeting({});
    const r = rbi({ stance: "neutral", dissent_count: 2 });
    const rows = historyRows([r, f], []);
    expect(rows.map((x) => x.slug)).toEqual(["fomc-2026-09-17", "mpc-2024-08-08"]);
    expect(rows[0]).toMatchObject({ bank: "Fed", decision: "Cut", rateAfter: "3.25–3.50", change: "−25", direction: "cut", stance: null, vote: "—", href: "/policy/fed/2026-09-17", year: "2026" });
    expect(rows[1]).toMatchObject({ bank: "RBI", decision: "Hold", rateAfter: "6.50", change: "0", stance: "Neutral", dissents: 2 });
    expect(rows[1].voteNote).toBe("Each member's vote is published in the minutes, due 22 Aug 2024.");
  });
});
