// The screener's row check against the dev project (2A-18; same idea as lib/queries/policy.devcheck.ts). Not part of
// `npm test`: run it with `npm run check:dev-rows` once 2A-17's views are pushed to dev and 2A-15 has written a snapshot,
// with NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY set to dev's URL and browser key (public by design).
// It only reads `api` views. A missing setting or an empty snapshot fails the check; it never skips.
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it } from "vitest";
import type { ApiDatabase } from "@/lib/queries/api";
import { readIndexMembers, readScreener, type EquityDb } from "@/lib/queries/equity";

function client(): EquityDb {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error("set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to the dev project's");
  return createClient<ApiDatabase, "api">(url, key, { db: { schema: "api" }, auth: { persistSession: false, autoRefreshToken: false } });
}

async function held(query: PromiseLike<{ count: number | null; error: { message: string } | null }>): Promise<number> {
  const { count, error } = await query;
  if (error) throw new Error(error.message);
  if (count === null) throw new Error("no count");
  return count;
}

describe("the dev project (2A-18)", () => {
  const db = client();

  it("readScreener returns every company the database holds, one row each", async () => {
    const rows = await readScreener(db);
    const count = await held(db.from("screener").select("slug", { count: "exact", head: true }));
    expect(rows.length).toBe(count);
    expect(rows.length, "has 2A-15 written a screener snapshot on dev?").toBeGreaterThan(0);
    expect(new Set(rows.map((r) => r.slug)).size).toBe(rows.length);
    expect(rows.every((r) => r.market && r.currency)).toBe(true);
  });

  it("readIndexMembers returns every member of every index", async () => {
    const members = await readIndexMembers(db);
    const count = await held(db.from("index_members").select("slug", { count: "exact", head: true }));
    expect(members.length).toBe(count);
    expect(members.length, "has 2A-01 stored the Nifty 50's members on dev?").toBeGreaterThan(0);
  });
});
