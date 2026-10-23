// The row-limit guard (A1, #6; Review 01, N2), against a real supabase-js client whose fetch stands in for the Data
// API and, like it, cuts every response at 1,000 rows without an error (tests/fake-postgrest.ts). The rows are test
// data shaped like the views, not real figures. The same check against the dev project is
// lib/queries/policy.devcheck.ts (`npm run check:dev-rows`).
import { describe, expect, it } from "vitest";
import {
  FED_TARGET_SERIES,
  POLICY_START,
  readDailySeries,
  readDecisions,
  readFedTargetSteps,
  readLatestDecision,
  readRbiRateSettings,
} from "@/lib/queries/policy";
import { IncompleteReadError, MAX_ROWS, readAll } from "@/lib/queries/read";
import { fakeClient, type Row, type Store } from "@/tests/fake-postgrest";

/** Every calendar day from `from` to `to`, inclusive (FRED's DFEDTARU/L are daily, seven days a week). */
function days(from: string, to: string): string[] {
  const out: string[] = [];
  for (let d = new Date(`${from}T00:00:00Z`); d <= new Date(`${to}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) {
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

/** A step series: `changes` maps the day a value starts to the value. */
function stepSeries(key: string, from: string, to: string, changes: Record<string, number>): Row[] {
  let value: number | null = null;
  return days(from, to).map((obs_date) => {
    value = changes[obs_date] ?? value;
    return { series_key: key, obs_date, value, label: null };
  });
}

/** api.observation_steps as 0011 defines it: the days a series' value changed, plus its latest day. */
function steps(observations: Row[]): Row[] {
  const byKey = new Map<string, Row[]>();
  for (const o of observations) byKey.set(String(o.series_key), [...(byKey.get(String(o.series_key)) ?? []), o]);
  const out: Row[] = [];
  for (const rows of byKey.values()) {
    rows.sort((a, b) => String(a.obs_date).localeCompare(String(b.obs_date)));
    rows.forEach((o, i) => {
      const isLatest = i === rows.length - 1;
      if (i === 0 || rows[i - 1].value !== o.value || isLatest) {
        out.push({ series_key: o.series_key, obs_date: o.obs_date, value: o.value, is_latest: isLatest });
      }
    });
  }
  return out;
}

const TODAY = "2026-10-23";
const SINCE_2019 = { from: POLICY_START, to: TODAY };

// Test data: change days and levels shaped like the Fed's moves, not a record of them.
const UPPER = { "2018-12-01": 2.5, "2019-08-01": 2.25, "2020-03-04": 1.75, "2020-03-16": 0.25, "2022-03-17": 0.5, "2024-09-19": 5.0, "2025-12-11": 3.75 };
const LOWER = { "2018-12-01": 2.25, "2019-08-01": 2.0, "2020-03-04": 1.5, "2020-03-16": 0, "2022-03-17": 0.25, "2024-09-19": 4.75, "2025-12-11": 3.5 };

function meeting(bank: "Fed" | "RBI", date: string, i: number): Row {
  const fed = bank === "Fed";
  return {
    slug: `${fed ? "fomc" : "mpc"}-${date}`, bank, status: "held", decision_date: date,
    decision_at: `${date}T${fed ? "18:00:00+00:00" : "04:30:00+00:00"}`, kind: "scheduled", is_rate_decision: true,
    policy_rate_pct: fed ? 4 + (i % 4) / 4 : 6.5 - (i % 3) / 4, policy_rate_change_bps: (i % 3) * 25 - 25,
    stance: fed ? null : "neutral", stance_display_label: fed ? null : "AI-extracted",
    attribution: fed ? "Source: Federal Reserve Board" : "Source: Reserve Bank of India",
    fed_target_lower_pct: fed ? 3.75 + (i % 4) / 4 : null, fed_target_upper_pct: fed ? 4 + (i % 4) / 4 : null,
  };
}

function settings(date: string, i: number): Row[] {
  return (["repo", date < "2022-04-08" ? "reverse_repo" : "sdf", "msf", "crr"] as const).map((instrument, k) => ({
    meeting_slug: `mpc-${date}`, bank: "RBI", decision_date: date, instrument, value_pct: 6.5 - k / 4 - (i % 3) / 4,
    change_bps: 0,
  }));
}

/** A store shaped like dev once B4 has loaded the series: two ~2,850-row step series and ~50 meetings a bank. */
function realisticStore(): Store {
  const observations = [
    ...stepSeries(FED_TARGET_SERIES.upper, "2018-12-01", TODAY, UPPER),
    ...stepSeries(FED_TARGET_SERIES.lower, "2018-12-01", TODAY, LOWER),
  ];
  const fedDates = days("2018-12-19", TODAY).filter((_, i) => i % 46 === 0);
  const rbiDates = days("2018-12-05", TODAY).filter((_, i) => i % 58 === 0);
  return {
    observations,
    observation_steps: steps(observations),
    policy_meetings: [
      ...fedDates.map((d, i) => meeting("Fed", d, i)),
      ...rbiDates.map((d, i) => meeting("RBI", d, i)),
      { ...meeting("Fed", "2026-12-09", 0), status: "scheduled", policy_rate_pct: null, fed_target_lower_pct: null, fed_target_upper_pct: null },
    ],
    policy_rate_settings: [
      ...rbiDates.flatMap((d, i) => settings(d, i)),
      { meeting_slug: `fomc-${fedDates[3]}`, bank: "Fed", decision_date: fedDates[3], instrument: "fed_target_upper", value_pct: 4, change_bps: 0 },
    ],
  };
}

/** Far more rows in range than one request returns, in every view a query reads (the guard's worst case). */
function oversizedStore(): Store {
  const many = days("2016-01-01", TODAY); // ~3,950 days
  const observations = many.map((obs_date, i) => ({ series_key: "fred.DGS10", obs_date, value: 4 + (i % 50) / 100, label: null }));
  // A step series that changes every day, so its steps view is as long as the series.
  const upper = many.map((obs_date, i) => ({ series_key: FED_TARGET_SERIES.upper, obs_date, value: i, label: null }));
  const lower = many.map((obs_date, i) => ({ series_key: FED_TARGET_SERIES.lower, obs_date, value: -i, label: null }));
  return {
    observations: [...observations, ...upper, ...lower],
    observation_steps: steps([...upper, ...lower]),
    policy_meetings: many.flatMap((d, i) => [meeting("Fed", d, i), meeting("RBI", d, i)]),
    policy_rate_settings: many.flatMap((d, i) => settings(d, i)),
  };
}

function inRange(rows: Row[], column: string, range: { from: string; to: string }): Row[] {
  return rows.filter((r) => String(r[column]) >= range.from && String(r[column]) <= range.to);
}

describe("the stand-in Data API", () => {
  it("cuts a long read at 1,000 rows without an error, as the hosted one does (max_rows)", async () => {
    const { db } = fakeClient(realisticStore());
    const { data, error, count } = await db
      .from("observations")
      .select("series_key, obs_date, value", { count: "exact" })
      .eq("series_key", FED_TARGET_SERIES.upper)
      .gte("obs_date", POLICY_START);
    expect(error).toBeNull();
    expect(count).toBeGreaterThan(2800); // ~2,850 rows since 2019
    expect(data).toHaveLength(MAX_ROWS); // cut, silently: the bug N2 names
  });
});

describe("readAll against the stand-in", () => {
  it("returns every row of a 2,850-row series, in order, by paging", async () => {
    const store = realisticStore();
    const { db, seen } = fakeClient(store);
    const rows = await readDailySeries(db, FED_TARGET_SERIES.upper, SINCE_2019);
    const expected = inRange(store.observations.filter((o) => o.series_key === FED_TARGET_SERIES.upper), "obs_date", SINCE_2019);
    expect(rows.length).toBe(expected.length);
    expect(rows.map((r) => r.obs_date)).toEqual(expected.map((r) => r.obs_date));
    expect(seen.length).toBe(Math.ceil(expected.length / MAX_ROWS));
    expect(seen.every((s) => s.countExact)).toBe(true);
  });

  it("fails loudly when the server sends fewer rows per page than asked and never the rest", async () => {
    const { db } = fakeClient(realisticStore(), 1000);
    // A query that forgets the offset: every page is the first page, so the rows never add up to the count.
    await expect(
      readAll("a query without .range()", () =>
        db.from("observations").select("obs_date", { count: "exact" }).eq("series_key", FED_TARGET_SERIES.upper).limit(1000),
      ),
    ).rejects.toThrow(IncompleteReadError);
  });

  it("fails loudly when the response carries no count", async () => {
    const { db } = fakeClient(realisticStore());
    await expect(
      readAll("a query without { count: 'exact' }", (from, to) =>
        db.from("observations").select("obs_date").eq("series_key", FED_TARGET_SERIES.upper).range(from, to),
      ),
    ).rejects.toThrow(/no exact count/);
  });

  it("passes a database error on rather than returning an empty list", async () => {
    const { db } = fakeClient(realisticStore());
    await expect(
      readAll("a misspelt column", (from, to) =>
        db.from("observations").select("obs_dte", { count: "exact" }).range(from, to),
      ),
    ).rejects.toThrow(/does not exist/);
  });
});

describe("the Fed target range band (N2)", () => {
  it("reads api.observation_steps, never api.observations", async () => {
    const { db, seen } = fakeClient(realisticStore());
    await readFedTargetSteps(db, SINCE_2019);
    expect(new Set(seen.map((s) => s.view))).toEqual(new Set(["observation_steps"]));
  });

  it("returns a few dozen change points, starting with the step in force when the range opens", async () => {
    const { db } = fakeClient(realisticStore());
    const { upper, lower } = await readFedTargetSteps(db, SINCE_2019);
    expect(upper.seriesKey).toBe("fred.DFEDTARU");
    expect(lower.seriesKey).toBe("fred.DFEDTARL");
    expect(upper.steps.map((s) => [s.obs_date, s.value])).toEqual([
      ["2018-12-01", 2.5], ["2019-08-01", 2.25], ["2020-03-04", 1.75], ["2020-03-16", 0.25],
      ["2022-03-17", 0.5], ["2024-09-19", 5], ["2025-12-11", 3.75], [TODAY, 3.75],
    ]);
    expect(upper.steps.at(-1)?.is_latest).toBe(true);
    expect(lower.steps.map((s) => s.value)).toEqual([2.25, 2, 1.5, 0, 0.25, 4.75, 3.5, 3.5]);
  });

  it("starts a later range with the value in force on its first day", async () => {
    const { db } = fakeClient(realisticStore());
    const { upper } = await readFedTargetSteps(db, { from: "2023-01-01", to: "2024-12-31" });
    expect(upper.steps.map((s) => [s.obs_date, s.value])).toEqual([["2022-03-17", 0.5], ["2024-09-19", 5]]);
  });

  it("refuses a malformed or backwards range", async () => {
    const { db } = fakeClient(realisticStore());
    await expect(readFedTargetSteps(db, { from: "2024-12-31", to: "2024-01-01" })).rejects.toThrow(RangeError);
    await expect(readFedTargetSteps(db, { from: "1 Jan 2024", to: TODAY })).rejects.toThrow(RangeError);
  });
});

describe("the RBI repo step and corridor", () => {
  it("reads the repo and corridor rates set at MPC meetings, with the settings in force when the range opens", async () => {
    const store = realisticStore();
    const { db, seen } = fakeClient(store);
    const range = { from: "2022-01-01", to: TODAY };
    const rows = await readRbiRateSettings(db, range);
    expect(new Set(seen.map((s) => s.view))).toEqual(new Set(["policy_rate_settings"]));
    const corridor = store.policy_rate_settings.filter((r) => r.bank === "RBI" && r.instrument !== "crr");
    const before = corridor.filter((r) => String(r.decision_date) < range.from).map((r) => String(r.decision_date)).sort().at(-1);
    expect(rows.length).toBe(inRange(corridor, "decision_date", range).length + 3);
    expect(rows.slice(0, 3).every((r) => r.decision_date === before)).toBe(true);
    expect(rows.some((r) => r.bank !== "RBI" || r.instrument === "crr")).toBe(false);
  });
});

describe("the StatRow (F22)", () => {
  it("reads both Fed bounds from api.policy_meetings, for the latest held decision", async () => {
    const store = realisticStore();
    const { db, seen } = fakeClient(store);
    const fed = await readLatestDecision(db, "Fed");
    const held = store.policy_meetings.filter((m) => m.bank === "Fed" && m.status === "held");
    const latest = held.map((m) => String(m.decision_date)).sort().at(-1);
    expect(fed?.decision_date).toBe(latest);
    expect(fed?.fed_target_lower_pct).toEqual(expect.any(Number));
    expect(fed?.fed_target_upper_pct).toEqual(expect.any(Number));
    expect(seen.map((s) => s.view)).toEqual(["policy_meetings"]);
    expect(seen[0].params.get("select")).toContain("fed_target_lower_pct");
    expect(seen[0].params.get("select")).toContain("fed_target_upper_pct");
  });

  it("returns null for a bank with no held decision", async () => {
    const { db } = fakeClient({ ...realisticStore(), policy_meetings: [] });
    expect(await readLatestDecision(db, "RBI")).toBeNull();
  });
});

describe("every chart and table query returns as many rows as the database holds for its range (N2)", () => {
  const store = oversizedStore();
  const ranges = [SINCE_2019, { from: "2016-01-01", to: TODAY }, { from: "2024-02-29", to: "2024-03-01" }];

  it.each(ranges)("readDailySeries %o", async (range) => {
    const { db } = fakeClient(store);
    const rows = await readDailySeries(db, "fred.DGS10", range);
    expect(rows.length).toBe(inRange(store.observations.filter((o) => o.series_key === "fred.DGS10"), "obs_date", range).length);
  });

  it.each(ranges)("readFedTargetSteps %o", async (range) => {
    const { db } = fakeClient(store);
    const { upper, lower } = await readFedTargetSteps(db, range);
    for (const [series, key] of [[upper, FED_TARGET_SERIES.upper], [lower, FED_TARGET_SERIES.lower]] as const) {
      const held = store.observation_steps.filter((s) => s.series_key === key);
      const carry = held.some((s) => String(s.obs_date) < range.from) ? 1 : 0;
      expect(series.steps.length).toBe(inRange(held, "obs_date", range).length + carry);
    }
  });

  it.each(ranges)("readRbiRateSettings %o", async (range) => {
    const { db } = fakeClient(store);
    const rows = await readRbiRateSettings(db, range);
    const corridor = store.policy_rate_settings.filter((r) => r.bank === "RBI" && r.instrument !== "crr");
    const carry = corridor.some((r) => String(r.decision_date) < range.from) ? 3 : 0;
    expect(rows.length).toBe(inRange(corridor, "decision_date", range).length + carry);
  });

  it.each(ranges)("readDecisions %o", async (range) => {
    const { db } = fakeClient(store);
    const both = await readDecisions(db, range);
    const rbi = await readDecisions(db, range, "RBI");
    const held = inRange(store.policy_meetings.filter((m) => m.status === "held" && m.is_rate_decision), "decision_date", range);
    expect(both.length).toBe(held.length);
    expect(rbi.length).toBe(held.filter((m) => m.bank === "RBI").length);
    expect(new Set(both.map((m) => m.slug)).size).toBe(both.length); // no page overlaps another
  });
});
