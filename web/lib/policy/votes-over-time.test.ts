// "How members voted over time" (#508): the strips are the meeting page's dots, the grid its cells, the gate fails closed.
import { describe, expect, it } from "vitest";
import { dotEmphasis, votesOverTime, votesOverTimeShown, VOTES_OVER_TIME_FIELD } from "@/lib/policy/votes-over-time";
import type { FieldPolicyRow, PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";

function meeting(slug: string, bank: "Fed" | "RBI", date: string, bps: number | null, over: Partial<PolicyMeetingRow> = {}): PolicyMeetingRow {
  return {
    slug, bank, status: "held", decision_date: date, decision_at: null, kind: "scheduled", is_rate_decision: true,
    meeting_no: null, minutes_due_on: null, policy_rate_pct: null, policy_rate_change_bps: bps, stance: null,
    stance_display_label: null, dissent_count: 0, attribution: null, fed_target_lower_pct: null, fed_target_upper_pct: null, ...over,
  };
}
function vote(slug: string, name: string, bps: number | null, over: Partial<PolicyVoteRow> = {}): PolicyVoteRow {
  return {
    meeting_slug: slug, member_slug: name.toLowerCase().replace(/\W+/g, "-"), member_name: name, voted_change_bps: bps,
    stance_vote: null, is_dissent: false, dissent_text: null, document_id: 7, char_start: 1, char_end: 9, ...over,
  };
}
const names = (n: number) => Array.from({ length: n }, (_, i) => `Member ${String.fromCharCode(65 + i)}`);
const six = ["Das", "Varma", "Patra", "Goyal", "Bhide", "Kumar"];

describe("votesOverTimeShown", () => {
  const row = (status: FieldPolicyRow["status"]): FieldPolicyRow => ({ field_key: VOTES_OVER_TIME_FIELD, status, label: null });
  it("is hidden with no row, no read or a hidden row, and shown for shown or labelled", () => {
    expect(votesOverTimeShown(null)).toBe(false);
    expect(votesOverTimeShown([])).toBe(false);
    expect(votesOverTimeShown([{ field_key: "site.policy_day", status: "shown", label: null }])).toBe(false);
    expect(votesOverTimeShown([row("hidden")])).toBe(false);
    expect(votesOverTimeShown([row("shown")])).toBe(true);
    expect(votesOverTimeShown([row("labelled")])).toBe(true);
  });
});

describe("votesOverTime", () => {
  it("orders the strips newest first and draws each meeting's dots as the meeting page does", () => {
    const decisions = [
      meeting("fomc-2020-03-15", "Fed", "2020-03-15", -100),
      meeting("fomc-2020-09-16", "Fed", "2020-09-16", 0),
      meeting("mpc-2025-02-07", "RBI", "2025-02-07", -25),
    ];
    const votes = [
      ...names(11).map((n) => vote("fomc-2020-03-15", n, -100)),
      ...names(10).map((n) => vote("fomc-2020-09-16", n, 0)),
      vote("fomc-2020-09-16", "Robert Kaplan", null, { is_dissent: true, dissent_text: "guidance" }),
      vote("fomc-2020-09-16", "Neel Kashkari", null, { is_dissent: true, dissent_text: "guidance" }),
    ];
    const fed = votesOverTime("Fed", decisions, votes);
    expect(fed.rows.map((r) => r.slug)).toEqual(["fomc-2020-09-16", "fomc-2020-03-15"]);
    expect(fed.axis).toMatchObject({ min: -100, max: 100 });
    const sep = fed.rows[0];
    if (sep.kind !== "dots") throw new Error("expected dots");
    expect(sep.dots.filter((d) => d.hollow).map((d) => d.member).sort()).toEqual(["Neel Kashkari", "Robert Kaplan"]);
    expect(sep.dots.filter((d) => d.hollow).every((d) => d.at === 0)).toBe(true);
    const mar = fed.rows[1];
    if (mar.kind !== "dots") throw new Error("expected dots");
    expect(new Set(mar.dots.map((d) => d.at))).toEqual(new Set([-100]));
    expect(mar.maxLevel).toBe(10);
    expect(votesOverTime("RBI", decisions, votes).rows.map((r) => r.slug)).toEqual(["mpc-2025-02-07"]);
  });

  it("puts the RBI's 35 and 40 bps moves where they are and a stance-only dissent hollow", () => {
    const decisions = [meeting("mpc-2022-05-04", "RBI", "2022-05-04", 40), meeting("mpc-2019-02-07", "RBI", "2019-02-07", -25), meeting("mpc-2019-12-05", "RBI", "2019-12-05", -35)];
    const votes = [
      ...six.map((n) => vote("mpc-2022-05-04", n, 40, { stance_vote: "withdrawal" })),
      ...six.map((n, i) => vote("mpc-2019-12-05", n, -35, { stance_vote: i === 0 ? "against" : "accommodative", is_dissent: i === 0 })),
      ...six.map((n) => vote("mpc-2019-02-07", n, -25)),
    ];
    const rbi = votesOverTime("RBI", decisions, votes);
    const by = (s: string) => rbi.rows.find((r) => r.slug === s)!;
    const may = by("mpc-2022-05-04");
    if (may.kind !== "dots") throw new Error("dots");
    expect(new Set(may.dots.map((d) => d.at))).toEqual(new Set([40]));
    expect(may.summary).toBe("6 for a 40 bps hike");
    const dec = by("mpc-2019-12-05");
    if (dec.kind !== "dots") throw new Error("dots");
    const hollow = dec.dots.filter((d) => d.hollow);
    expect(hollow).toHaveLength(1);
    expect(hollow[0]).toMatchObject({ member: "Das", at: -35 });
    expect(rbi.stance).toBe(true);
  });

  it("shows a meeting without all its votes as words, never as an older meeting's dots", () => {
    const decisions = [meeting("mpc-2026-10-07", "RBI", "2026-10-07", 0, { minutes_due_on: "2026-10-21" }), meeting("mpc-2026-08-06", "RBI", "2026-08-06", 0)];
    const votes = six.map((n) => vote("mpc-2026-08-06", n, 0));
    const rbi = votesOverTime("RBI", decisions, votes);
    const pending = rbi.rows[0];
    expect(pending.slug).toBe("mpc-2026-10-07");
    expect(pending.kind).toBe("note");
    expect(rbi.rows[1].kind).toBe("dots");
    expect(rbi.meetings.map((m) => m.slug)).toEqual(["mpc-2026-08-06"]);
    expect(rbi.cells.has("mpc-2026-10-07")).toBe(false);
  });

  it("builds the member × meeting grid from the meeting page's own cells, a dissent marked", () => {
    const decisions = [meeting("fomc-2025-09-17", "Fed", "2025-09-17", -25), meeting("fomc-2025-07-30", "Fed", "2025-07-30", 0)];
    const votes = [
      vote("fomc-2025-09-17", "Ann Lee", -25), vote("fomc-2025-09-17", "Bo Ray", -50, { is_dissent: true }),
      vote("fomc-2025-07-30", "Ann Lee", 0),
    ];
    const fed = votesOverTime("Fed", decisions, votes);
    expect(fed.members.map((m) => m.name)).toEqual(["Ann Lee", "Bo Ray"]);
    expect(fed.meetings.map((m) => m.date)).toEqual(["2025-09-17", "2025-07-30"]);
    expect(fed.cells.get("fomc-2025-09-17")!.get("bo-ray")).toMatchObject({ vote: "−50", dissent: "preferred −50 bps" });
    expect(fed.cells.get("fomc-2025-07-30")!.get("bo-ray")).toBeUndefined(); // no vote recorded there: the grid shows a dash
  });

  it("takes every vote of a long history (more than 1,000 rows) and drops none", () => {
    const decisions = Array.from({ length: 100 }, (_, i) => meeting(`fomc-${i}`, "Fed", `2019-${String((i % 12) + 1).padStart(2, "0")}-${String((i % 28) + 1).padStart(2, "0")}`, 0));
    const votes = decisions.flatMap((d) => names(12).map((n) => vote(d.slug, n, 0)));
    expect(votes.length).toBeGreaterThan(1000);
    const fed = votesOverTime("Fed", decisions, votes);
    expect(fed.rows).toHaveLength(100);
    expect(fed.rows.every((r) => r.kind === "dots" && r.dots.length === 12)).toBe(true);
  });

  it("returns no rows for a bank with no held decisions", () => {
    const out = votesOverTime("Fed", [meeting("mpc-1", "RBI", "2025-01-01", 0)], []);
    expect(out.rows).toEqual([]);
    expect(out.latest).toBeNull();
  });
});

describe("dotEmphasis", () => {
  it("rings the picked-out member's dots and dims the others", () => {
    expect(dotEmphasis(null, "a")).toBe("plain");
    expect(dotEmphasis("a", "a")).toBe("mine");
    expect(dotEmphasis("a", "b")).toBe("dim");
  });
});
