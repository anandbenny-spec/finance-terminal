// web/lib/queries/accuracy.ts: the /accuracy page's reads (#135): api.quality_summary and api.eval_summary, whole or
// not at all (readAll, exact counts), cached for minutes like the shell's. A failed read returns `{ ok: false }` and
// the page shows its error state; the reason goes to the server log only (visitors never see errors: Arch 02).
// The `load…` functions take the client so a test can run them against the Data API stand-in (tests/fake-postgrest.ts).
import { cacheLife, cacheTag } from "next/cache";
import type { EvalSummaryRow, QualitySummaryRow } from "@/lib/queries/api";
import { readAll } from "@/lib/queries/read";
import type { Read } from "@/lib/queries/shell";
import { publicClient, type PublicClient } from "@/lib/supabase/public";

function failed(what: string, error: unknown): { ok: false } {
  console.error(`[accuracy] ${what} read failed: ${error instanceof Error ? error.message : String(error)}`);
  return { ok: false };
}

/** Every quality_summary row (about 16 measures × 3 periods per module), checked against the exact count. */
export function loadQualitySummary(db: PublicClient): Promise<QualitySummaryRow[]> {
  return readAll<QualitySummaryRow>("api.quality_summary", (from, to) =>
    db
      .from("quality_summary")
      .select("module, period, measure, value, n, label, as_of", { count: "exact" })
      .order("module")
      .order("period")
      .order("measure")
      .range(from, to),
  );
}

/** Every passing evaluation run, checked against the exact count. */
export function loadEvalSummary(db: PublicClient): Promise<EvalSummaryRow[]> {
  return readAll<EvalSummaryRow>("api.eval_summary", (from, to) =>
    db
      .from("eval_summary")
      .select("eval_set, model, metric, value, n_items, run_at", { count: "exact" })
      .order("eval_set")
      .order("model")
      .order("metric")
      .range(from, to),
  );
}

export async function readQualitySummary(): Promise<Read<{ rows: QualitySummaryRow[] }>> {
  "use cache";
  cacheLife("minutes");
  cacheTag("accuracy");
  const db = publicClient();
  if (!db) return failed("quality summary", "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
  try {
    return { ok: true, rows: await loadQualitySummary(db) };
  } catch (error) {
    return failed("quality summary", error);
  }
}

export async function readEvalSummary(): Promise<Read<{ rows: EvalSummaryRow[] }>> {
  "use cache";
  cacheLife("minutes");
  cacheTag("accuracy");
  const db = publicClient();
  if (!db) return failed("eval summary", "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
  try {
    return { ok: true, rows: await loadEvalSummary(db) };
  } catch (error) {
    return failed("eval summary", error);
  }
}
