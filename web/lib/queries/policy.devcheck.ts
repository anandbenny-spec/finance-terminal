// The row-limit check against a real project (A1, #6; N2's done-when): every chart and table query returns as many
// rows as the database holds for its range. Not part of `npm test` (CI has no project to read): run it with
// `npm run check:dev-rows` once B4 has loaded the series on dev, with NEXT_PUBLIC_SUPABASE_URL and
// NEXT_PUBLIC_SUPABASE_ANON_KEY set to dev's URL and browser key (public by design). It only reads `api` views.
// A missing setting or an unloaded series fails the check; it never skips.
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import type { ApiDatabase } from "@/lib/queries/api";
import {
  FED_TARGET_SERIES,
  POLICY_START,
  RBI_CORRIDOR_INSTRUMENTS,
  readDailySeries,
  readDecisions,
  readFedTargetSteps,
  readRbiRateSettings,
  type PolicyDb,
} from "@/lib/queries/policy";
import { MAX_ROWS } from "@/lib/queries/read";

function client(): PolicyDb {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error("set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to the dev project's");
  return createClient<ApiDatabase, "api">(url, key, {
    db: { schema: "api" },
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

/** The database's own count for a filter (`head`: no rows, just Content-Range). */
async function held(query: PromiseLike<{ count: number | null; error: { message: string } | null }>): Promise<number> {
  const { count, error } = await query;
  if (error) throw new Error(error.message);
  if (count === null) throw new Error("no count");
  return count;
}

const range = { from: POLICY_START, to: new Date().toISOString().slice(0, 10) };

describe("the dev project (N2)", () => {
  const db = client();

  it("holds the Fed's daily series in full, and the Data API cuts a whole read of one at max_rows", async () => {
    const { data, count, error } = await db
      .from("observations")
      .select("obs_date", { count: "exact" })
      .eq("series_key", FED_TARGET_SERIES.upper)
      .gte("obs_date", range.from);
    expect(error).toBeNull();
    expect(count, "DFEDTARU since 2019 should hold ~2,850 rows: has B4 loaded the series?").toBeGreaterThan(MAX_ROWS);
    expect(data?.length).toBe(MAX_ROWS);
  });

  it("readDailySeries returns every DFEDTARU row since 2019", async () => {
    const rows = await readDailySeries(db, FED_TARGET_SERIES.upper, range);
    const count = await held(
      db.from("observations").select("obs_date", { count: "exact", head: true })
        .eq("series_key", FED_TARGET_SERIES.upper).gte("obs_date", range.from).lte("obs_date", range.to),
    );
    expect(rows.length).toBe(count);
  });

  it("readFedTargetSteps returns every change point of both bounds, plus the step in force on 1 Jan 2019", async () => {
    const steps = await readFedTargetSteps(db, range);
    for (const series of [steps.upper, steps.lower]) {
      const inRange = await held(
        db.from("observation_steps").select("obs_date", { count: "exact", head: true })
          .eq("series_key", series.seriesKey).gte("obs_date", range.from).lte("obs_date", range.to),
      );
      const before = await held(
        db.from("observation_steps").select("obs_date", { count: "exact", head: true })
          .eq("series_key", series.seriesKey).lt("obs_date", range.from),
      );
      expect(series.steps.length).toBe(inRange + Math.min(before, 1));
      expect(series.steps.length).toBeLessThan(100); // a few dozen, not ~2,850
    }
  });

  it("readRbiRateSettings and readDecisions return every row for the range", async () => {
    const settings = await readRbiRateSettings(db, range);
    const inRange = await held(
      db.from("policy_rate_settings").select("decision_date", { count: "exact", head: true })
        .eq("bank", "RBI").in("instrument", [...RBI_CORRIDOR_INSTRUMENTS])
        .gte("decision_date", range.from).lte("decision_date", range.to),
    );
    expect(settings.filter((s) => s.decision_date >= range.from).length).toBe(inRange);

    const decisions = await readDecisions(db, range);
    const meetings = await held(
      db.from("policy_meetings").select("slug", { count: "exact", head: true })
        .eq("status", "held").eq("is_rate_decision", true)
        .gte("decision_date", range.from).lte("decision_date", range.to),
    );
    expect(decisions.length).toBe(meetings);
  });
});
