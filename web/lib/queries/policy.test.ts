// The row-limit guard (A1, #6; Review 01, N2), against a real supabase-js client whose fetch stands in for the Data
// API and, like it, cuts every response at 1,000 rows without an error (tests/fake-postgrest.ts). The rows are test
// data shaped like the views, not real figures. The same check against the dev project is
// lib/queries/policy.devcheck.ts (`npm run check:dev-rows`).
import { describe, expect, it } from "vitest";
import {
  FED_TARGET_SERIES,
  POLICY_START,
  readAdjacentMeetings,
  readAiOutput,
  readChunks,
  readCitations,
  readDiff,
  readMeeting,
  readMeetingDocuments,
  readMeetingSettings,
  readProjections,
  readDailySeries,
  readDecisionDocument,
  readFieldPolicy,
  readSeriesInfo,
  readVotes,
  readDecisions,
  readFedTargetSteps,
  readLatestDecision,
  readRbiRateSettings,
  readReactions,
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
    meeting_no: fed ? null : 14 + i, minutes_due_on: null, dissent_count: 0,
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

// ------------------------------------------------------------------ C3's reads (the /policy page)

function vote(slug: string, k: number): Row {
  return {
    meeting_slug: slug, member_slug: `m${String(k).padStart(2, "0")}`, member_name: `Member ${k}`,
    voted_change_bps: -25, stance_vote: null, is_dissent: false, dissent_text: null, document_id: 1, char_start: 0, char_end: 10,
  };
}

describe("C3's reads keep the row-limit guard (N2)", () => {
  it("reads every vote at 120 meetings × 12 voters (1,440 rows), batch by batch, none lost or doubled", async () => {
    const slugs = days("2019-01-01", "2026-12-31").filter((_, i) => i % 24 === 0).slice(0, 120).map((d) => `fomc-${d}`);
    const store: Store = { policy_votes: [...slugs.flatMap((s) => Array.from({ length: 12 }, (_, k) => vote(s, k))), vote("fomc-2018-12-19", 0)] };
    const { db, seen } = fakeClient(store);
    const rows = await readVotes(db, slugs);
    expect(rows).toHaveLength(1440);
    expect(new Set(rows.map((r) => `${r.meeting_slug}/${r.member_slug}`)).size).toBe(1440);
    expect(rows.some((r) => r.meeting_slug === "fomc-2018-12-19")).toBe(false);
    expect(seen.every((r) => r.countExact)).toBe(true);
  });

  it("reads the votes for no meetings as none, without a request", async () => {
    const { db, seen } = fakeClient({ policy_votes: [] });
    expect(await readVotes(db, [])).toEqual([]);
    expect(seen).toHaveLength(0);
  });

  it("reads a meeting's decision document at its latest version", async () => {
    const doc = (id: number, version_no: number, doc_type = "fomc_statement"): Row => ({
      id, doc_type, doc_type_label: "FOMC statement", entity_slug: "fed", event_slug: "fomc-2026-09-17", title: "FOMC statement",
      source_url: "https://www.federalreserve.gov/x.htm", attribution: "Source: Federal Reserve Board", published_at: "2026-09-17T18:00:00+00:00", version_no,
    });
    const { db } = fakeClient({ documents: [doc(1, 1), doc(2, 2), doc(3, 1, "fomc_minutes")] });
    expect((await readDecisionDocument(db, "fomc-2026-09-17", "Fed"))?.id).toBe(2);
    expect(await readDecisionDocument(db, "fomc-2026-07-30", "Fed")).toBeNull();
  });

  it("reads an AI output by document or by meeting, and every citation of it", async () => {
    const out = (id: number, task: string, document_id: number | null, event_slug: string | null): Row => ({
      id, task, document_id, event_slug, scope: "", body: "b", output_label: null, content: null, display_label: "AI summary",
      model: "claude-haiku-5-5", created_at: "2026-09-17T18:30:00+00:00",
    });
    const citations = Array.from({ length: 1100 }, (_, k): Row => ({
      ai_output_id: 1, citation_no: k + 1, claim_path: "/bullets/0", document_id: 2, document_title: "t", source_url: null,
      char_start: k, char_end: k + 1, quote: "q",
    }));
    const { db } = fakeClient({ ai_outputs: [out(1, "doc_summary", 2, null), out(3, "stance_extraction", null, "mpc-2026-10-07")], ai_citations: citations });
    expect((await readAiOutput(db, "doc_summary", { documentId: 2 }))?.id).toBe(1);
    expect((await readAiOutput(db, "stance_extraction", { eventSlug: "mpc-2026-10-07" }))?.id).toBe(3);
    expect(await readAiOutput(db, "doc_summary", { documentId: 9 })).toBeNull();
    expect(await readCitations(db, 1)).toHaveLength(1100);
  });

  it("reads the field policy and the series' source lines whole", async () => {
    const { db } = fakeClient({
      field_policy: [{ field_key: "ai.doc_summary", status: "labelled", label: "AI summary" }],
      series: [{ key: FED_TARGET_SERIES.upper, name: "u", market: "US", attribution: "Source: FRED, Federal Reserve Bank of St. Louis" }, { key: "x", name: "x", market: null, attribution: null }],
    });
    expect(await readFieldPolicy(db)).toHaveLength(1);
    expect((await readSeriesInfo(db, [FED_TARGET_SERIES.upper])).map((s) => s.key)).toEqual([FED_TARGET_SERIES.upper]);
  });
});

// ------------------------------------------------------------------ Pass 1b's reads (#70: decision-day moves)

describe("Pass 1b's reads keep the row-limit guard (N2)", () => {
  it("reads every market reaction whole (1,200 rows, over one request's cap), in order, none lost or doubled", async () => {
    const keys = ["fred.DGS2", "fred.DGS10", "yf.^GSPC", "fred.DEXINUS", "yf.^NSEI", "yf.^NSEBANK"];
    const slugs = days("2019-01-01", "2026-12-31").filter((_, i) => i % 14 === 0).slice(0, 200).map((d) => `fomc-${d}`);
    const reactions = slugs.flatMap((meeting_slug) =>
      keys.map((series_key): Row => ({
        meeting_slug, series_key, series_name: series_key, pre_obs_date: "2024-09-17", post_obs_date: "2024-09-18",
        pre_value: 1, post_value: 2, change: 1, change_unit: "bps", is_surprise_measure: series_key === "fred.DGS2", attribution: null,
      })),
    );
    const { db, seen } = fakeClient({ market_reactions: [...reactions].reverse() });
    const rows = await readReactions(db);
    expect(rows).toHaveLength(1200);
    expect(new Set(rows.map((r) => `${r.meeting_slug}/${r.series_key}`)).size).toBe(1200);
    expect(rows[0].meeting_slug <= rows[1199].meeting_slug).toBe(true);
    expect(seen.length).toBeGreaterThan(1);
    expect(seen.every((r) => r.countExact)).toBe(true);
  });
});

// ------------------------------------------------------------------ C4's reads (the meeting pages)

describe("C4's reads keep the row-limit guard (N2)", () => {
  it("reads a meeting whatever its status, and its neighbours of the same bank (cancelled skipped)", async () => {
    const store = realisticStore();
    const fed: Row[] = store.policy_meetings.filter((m) => m.bank === "Fed").map((m) => ({ ...m, has_press_conference: true }));
    const target = fed[5];
    const cancelled = { ...fed[4], slug: "fomc-cancelled", decision_date: String(fed[5].decision_date).replace(/-\d\d$/, "-01"), status: "cancelled" };
    const { db } = fakeClient({ policy_meetings: [...fed, cancelled, ...store.policy_meetings.filter((m) => m.bank === "RBI").map((m) => ({ ...m, has_press_conference: null }))] });
    expect((await readMeeting(db, String(target.slug)))?.slug).toBe(target.slug);
    expect(await readMeeting(db, "fomc-1999-01-01")).toBeNull();
    const { previous, next } = await readAdjacentMeetings(db, "Fed", String(target.decision_date));
    expect(previous?.slug).toBe(fed[4].slug);
    expect(next?.slug).toBe(fed[6].slug);
    const scheduled = fed.find((m) => m.status === "scheduled")!;
    expect((await readAdjacentMeetings(db, "Fed", String(scheduled.decision_date))).next).toBeNull();
  });

  it("reads a long transcript's chunks whole (1,200 chunks), in order", async () => {
    const chunks = Array.from({ length: 1200 }, (_, k): Row => ({
      document_id: 4, chunk_no: k + 1, char_start: k * 10, char_end: k * 10 + 10, section: null, speaker: null, body: "0123456789",
    }));
    const { db } = fakeClient({ document_chunks: [...chunks].reverse() });
    const rows = await readChunks(db, 4);
    expect(rows).toHaveLength(1200);
    expect(rows.map((r) => r.chunk_no)).toEqual(chunks.map((c) => c.chunk_no));
  });

  it("reads a meeting's rates, documents, projections and diff", async () => {
    const setting: Row = { meeting_slug: "mpc-2026-10-07", bank: "RBI", decision_date: "2026-10-07", instrument: "repo", value_pct: 5.5, change_bps: 25, crosscheck: null, document_id: 9, char_start: 1, char_end: 2 };
    const doc = (id: number, doc_type: string): Row => ({ id, doc_type, doc_type_label: doc_type, entity_slug: "rbi", event_slug: "mpc-2026-10-07", title: "t", source_url: null, attribution: null, published_at: null, version_no: 1 });
    const forecast = (series_key: string, vintage_date: string): Row => ({ series_key, series_name: "x", vintage_date, target_label: "Q3", target_start: null, target_end: null, value: 4, value_low: null, value_high: null, label: null, document_id: 9, char_start: 1, char_end: 2 });
    const { db } = fakeClient({
      policy_rate_settings: [setting, { ...setting, meeting_slug: "mpc-2026-08-06" }],
      documents: [doc(9, "mpc_resolution"), doc(10, "mpc_minutes"), { ...doc(11, "fomc_statement"), event_slug: "fomc-2026-09-17" }],
      forecasts: [forecast("rbi.proj_cpi", "2026-10-07"), forecast("rbi.proj_gdp", "2026-10-07"), forecast("rbi.proj_cpi", "2026-08-06"), forecast("fred.X", "2026-10-07")],
      document_diffs: [{ document_id: 9, base_document_id: 7, segments: null, words_added: 3, words_removed: 1 }],
    });
    expect(await readMeetingSettings(db, "mpc-2026-10-07")).toHaveLength(1);
    expect((await readMeetingDocuments(db, "mpc-2026-10-07")).map((d) => d.id)).toEqual([10, 9]);
    expect((await readProjections(db, "2026-10-07")).map((f) => f.series_key)).toEqual(["rbi.proj_cpi", "rbi.proj_gdp"]);
    expect((await readDiff(db, 9))?.base_document_id).toBe(7);
    expect(await readDiff(db, 10)).toBeNull();
  });
});
