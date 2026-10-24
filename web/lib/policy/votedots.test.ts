// VoteDotPlot's model (Design 01 §1.14; F23; Spec 01 §5.2 item 1): exact positions, the axis, hollow dissents.
import { describe, expect, it } from "vitest";
import type { PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import { axisFor, dotPlot, summaryLine } from "@/lib/policy/votedots";

function meeting(over: Partial<PolicyMeetingRow>): PolicyMeetingRow {
  return {
    slug: "m", bank: "Fed", status: "held", decision_date: "2020-03-15", decision_at: null, kind: "scheduled",
    is_rate_decision: true, meeting_no: null, minutes_due_on: null, policy_rate_pct: null, policy_rate_change_bps: -25,
    stance: null, stance_display_label: null, dissent_count: 0, attribution: null, fed_target_lower_pct: null,
    fed_target_upper_pct: null, ...over,
  };
}

function vote(name: string, bps: number | null, over: Partial<PolicyVoteRow> = {}): PolicyVoteRow {
  return {
    meeting_slug: "m", member_slug: name.toLowerCase(), member_name: name, voted_change_bps: bps, stance_vote: null,
    is_dissent: false, dissent_text: null, document_id: 7, char_start: 10, char_end: 20, ...over,
  };
}

describe("axisFor", () => {
  it("is symmetric, ticks every 25 bps and at least ±50", () => {
    expect(axisFor([0, 25])).toEqual({ min: -50, max: 50, ticks: [-50, -25, 0, 25, 50] });
    expect(axisFor([-100]).ticks).toHaveLength(9);
    expect(axisFor([-40]).min).toBe(-50);
    expect(axisFor([-35, 0]).max).toBe(50);
    expect(axisFor([-60]).max).toBe(75);
  });
});

describe("dotPlot", () => {
  it("places each member at their exact vote and stacks equal votes upward", () => {
    const m = meeting({ policy_rate_change_bps: -25 });
    const plot = dotPlot(m, [vote("A", -25), vote("B", -25), vote("C", -50, { is_dissent: true }), vote("D", -25)])!;
    const at = (n: string) => plot.dots.find((d) => d.member === n)!;
    expect(at("C").at).toBe(-50);
    expect(at("A").level).toBe(0);
    expect(at("B").level).toBe(1);
    expect(at("D").level).toBe(2);
    expect(plot.maxLevel).toBe(2);
    expect(plot.dots.every((d) => !d.hollow)).toBe(true);
    expect(plot.summary).toBe("3 for a 25 bps cut · 1 preferred a 50 bps cut");
  });

  it("draws the RBI's off-grid moves where they are (35 and 40 bps)", () => {
    const m = meeting({ bank: "RBI", policy_rate_change_bps: -35 });
    const votes = ["A", "B", "C", "D", "E", "F"].map((n) => vote(n, -35, { stance_vote: "accommodative" }));
    const plot = dotPlot(m, votes)!;
    expect(new Set(plot.dots.map((d) => d.at))).toEqual(new Set([-35]));
    expect(plot.ticks).toEqual([-50, -25, 0, 25, 50]);
    expect(plot.summary).toBe("6 for a 35 bps cut");
    const m40 = meeting({ bank: "RBI", policy_rate_change_bps: -40 });
    expect(dotPlot(m40, votes.map((v) => ({ ...v, voted_change_bps: -40 })))!.summary).toBe("6 for a 40 bps cut");
  });

  it("widens the axis for the Fed's −100 in March 2020", () => {
    const plot = dotPlot(meeting({ policy_rate_change_bps: -100 }), [vote("A", -100), vote("B", -100)])!;
    expect([plot.min, plot.max]).toEqual([-100, 100]);
  });

  it("draws a dissent that isn't about the rate hollow, at the decision (Sep 2020 guidance; RBI stance)", () => {
    const m = meeting({ policy_rate_change_bps: 0 });
    const plot = dotPlot(m, [vote("A", 0), vote("B", 0, { is_dissent: true }), vote("C", null, { is_dissent: true })])!;
    const hollow = plot.dots.filter((d) => d.hollow).map((d) => d.member);
    expect(hollow).toEqual(["B", "C"]);
    expect(plot.dots.find((d) => d.member === "C")!.at).toBe(0);
    expect(plot.summary).toBe("1 for holding · 2 dissented, not on the rate");
    expect(plot.dots.find((d) => d.member === "B")!.lines).toContain("Dissent, not on the rate");
  });

  it("names the RBI stance vote and cites the passage", () => {
    const m = meeting({ bank: "RBI", policy_rate_change_bps: 0 });
    const votes = ["A", "B", "C", "D", "E"].map((n) => vote(n, 0, { stance_vote: "neutral" }));
    votes.push(vote("F", 0, { stance_vote: "against", is_dissent: true }));
    const plot = dotPlot(m, votes)!;
    const f = plot.dots.find((d) => d.member === "F")!;
    expect(f.hollow).toBe(true);
    expect(f.lines).toEqual(["Voted to hold", "voted against the stance", "Dissent, not on the rate: voted against the stance"]);
    expect(f.href).toBe("/documents/7?span=10-20#cited");
  });

  it("declines to draw what it can't place truthfully", () => {
    const m = meeting({});
    expect(dotPlot(m, [])).toBeNull();
    expect(dotPlot(meeting({ policy_rate_change_bps: null }), [vote("A", 0)])).toBeNull();
    expect(dotPlot(m, [vote("A", null)])).toBeNull(); // no rate vote and no stored dissent
    expect(dotPlot(meeting({ bank: "RBI" }), [vote("A", -25)])).toBeNull(); // fewer than six members
  });

  it("ignores other meetings' rows", () => {
    expect(dotPlot(meeting({}), [vote("A", -25, { meeting_slug: "other" })])).toBeNull();
  });
});

describe("summaryLine", () => {
  it("words holds and hikes", () => {
    expect(summaryLine(25, [vote("A", 25), vote("B", 0, { is_dissent: true })])).toBe("1 for a 25 bps hike · 1 preferred to hold");
  });
});
