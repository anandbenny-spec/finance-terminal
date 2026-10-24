// NumbersChanged's items (Spec 01 §4.3; Spec 01 §5.2 item 3): the same target period across vintages, rates only
// where both meetings have them, the stance behind its field policy, no guessing. Test rows, not real meetings.
import { describe, expect, it } from "vitest";
import type { ForecastRow, PolicyMeetingRow, PolicyRateSettingRow, PolicyVoteRow } from "@/lib/queries/api";
import { numbersChanged, type NumbersInput } from "@/lib/policy/numbers";

function meeting(over: Partial<PolicyMeetingRow>): PolicyMeetingRow {
  return {
    slug: "mpc-b", bank: "RBI", status: "held", decision_date: "2026-10-07", decision_at: null, kind: "scheduled", is_rate_decision: true,
    meeting_no: null, minutes_due_on: null, policy_rate_pct: 5.5, policy_rate_change_bps: 0, stance: "neutral", stance_display_label: null,
    dissent_count: 0, attribution: null, fed_target_lower_pct: null, fed_target_upper_pct: null, ...over,
  };
}
const setting = (slug: string, instrument: PolicyRateSettingRow["instrument"], value: number): PolicyRateSettingRow => ({
  meeting_slug: slug, bank: "RBI", decision_date: "2026-10-07", instrument, value_pct: value, change_bps: 0,
});
const proj = (key: string, label: string, value: number | null, low: number | null = null, high: number | null = null): ForecastRow => ({
  series_key: key, series_name: key, vintage_date: "2026-10-07", target_label: label, target_start: null, target_end: null,
  value, value_low: low, value_high: high, label: null, document_id: 1, char_start: 0, char_end: 1,
});
const vote = (slug: string, name: string, bps: number, dissent = false): PolicyVoteRow => ({
  meeting_slug: slug, member_slug: name, member_name: name, voted_change_bps: bps, stance_vote: null, is_dissent: dissent, dissent_text: null,
});

const base = (over: Partial<NumbersInput> = {}): NumbersInput => ({
  current: meeting({}), previous: meeting({ slug: "mpc-a", decision_date: "2026-08-06", stance: "accommodative" }),
  currentSettings: [], previousSettings: [], currentProjections: [], previousProjections: [], currentVotes: [], previousVotes: [],
  stance: { show: true, label: "AI-extracted" }, ...over,
});

describe("RBI", () => {
  it("compares the rates both meetings set, with a signed change in bps", () => {
    const items = numbersChanged(
      base({
        currentSettings: [setting("mpc-b", "repo", 5.25), setting("mpc-b", "msf", 5.5), setting("mpc-b", "crr", 4)],
        previousSettings: [setting("mpc-a", "repo", 5.5), setting("mpc-a", "msf", 5.75)],
      }),
    );
    const repo = items.find((x) => x.key === "repo")!;
    expect([repo.previous, repo.next, repo.change, repo.changed]).toEqual(["5.50%", "5.25%", "−25 bps", true]);
    expect(items.find((x) => x.key === "msf")!.change).toBe("−25 bps");
    expect(items.find((x) => x.key === "crr")).toBeUndefined(); // not in the earlier meeting: left out
  });

  it("marks an unchanged rate as unchanged", () => {
    const [repo] = numbersChanged(base({ stance: { show: false, label: null }, currentSettings: [setting("mpc-b", "repo", 5.5)], previousSettings: [setting("mpc-a", "repo", 5.5)] }));
    expect(repo.changed).toBe(false);
    expect(repo.change).toBe("0 bps");
  });

  it("compares projections for the same target period only", () => {
    const items = numbersChanged(
      base({
        stance: { show: false, label: null },
        currentProjections: [proj("rbi.proj_cpi", "Q3 FY27", 4.1), proj("rbi.proj_cpi", "Q4 FY27", 4.4), proj("rbi.proj_gdp", "FY27", null, 6.5, 6.9)],
        previousProjections: [proj("rbi.proj_cpi", "Q3 FY27", 3.9), proj("rbi.proj_cpi", "Q1 FY27", 3.5), proj("rbi.proj_gdp", "FY27", null, 6.5, 6.8)],
      }),
    );
    expect(items.map((x) => x.key)).toEqual(["rbi.proj_cpi-Q3 FY27", "rbi.proj_gdp-FY27"]); // Q4 has no earlier counterpart
    const cpi = items[0];
    expect([cpi.label, cpi.previous, cpi.next, cpi.change, cpi.changed]).toEqual(["CPI inflation, Q3 FY27", "3.9%", "4.1%", "+0.2 pp", true]);
    const gdp = items[1];
    expect([gdp.previous, gdp.next, gdp.change, gdp.changed]).toEqual(["6.5–6.8%", "6.5–6.9%", "—", true]);
  });

  it("rounds a projection change away from floating-point noise", () => {
    const item = numbersChanged(base({ currentProjections: [proj("rbi.proj_cpi", "Q1", 4.3)], previousProjections: [proj("rbi.proj_cpi", "Q1", 4.1)] })).find((x) => x.key.startsWith("rbi"))!;
    expect(item.change).toBe("+0.2 pp");
  });

  it("shows the stance with its qualifier, and holds it back when its field policy hides it", () => {
    const [stance] = numbersChanged(base());
    expect([stance.previous, stance.next, stance.qualifier, stance.changed, stance.change]).toEqual(["Accommodative", "Neutral", "AI-extracted", true, null]);
    expect(numbersChanged(base({ stance: { show: false, label: null } }))).toEqual([]);
    expect(numbersChanged(base({ current: meeting({ stance: null }) }))).toEqual([]);
  });
});

describe("Fed", () => {
  const fed = (slug: string, over: Partial<PolicyMeetingRow> = {}) => meeting({ slug, bank: "Fed", ...over });
  const range = (slug: string, lo: number, up: number): PolicyRateSettingRow[] => [
    { ...setting(slug, "fed_target_lower", lo), bank: "Fed" },
    { ...setting(slug, "fed_target_upper", up), bank: "Fed" },
  ];

  it("compares the target range as one item and the vote tally", () => {
    const items = numbersChanged(
      base({
        current: fed("f2"), previous: fed("f1"),
        currentSettings: range("f2", 3.25, 3.5), previousSettings: range("f1", 3.5, 3.75),
        currentVotes: [vote("f2", "A", -25), vote("f2", "B", -25), vote("f2", "C", -50, true)],
        previousVotes: [vote("f1", "A", 0), vote("f1", "B", 0), vote("f1", "C", 0)],
      }),
    );
    expect(items.map((x) => x.key)).toEqual(["fed_target", "vote"]);
    expect([items[0].previous, items[0].next, items[0].change]).toEqual(["3.50–3.75%", "3.25–3.50%", "−25 bps"]);
    expect(items[1].changed).toBe(true);
  });

  it("leaves out what either meeting lacks", () => {
    expect(numbersChanged(base({ current: fed("f2"), previous: fed("f1"), currentSettings: range("f2", 3.25, 3.5) }))).toEqual([]);
  });
});
