// web/lib/queries/shell.ts: the shell's reads (C2): the ticker tape, the status bar's countdowns and its freshness.
//
// Each reads an `api` view with the browser key (lib/supabase/public.ts), whole or not at all (readAll), and is
// cached for minutes ('use cache', the `minutes` profile: served for up to a minute, then refreshed in the
// background), so the shell is prerendered and a decision stored by `watch` reaches it within a couple of minutes.
// A failed read returns `{ ok: false }` and the shell shows its error state; the reason goes to the server log only
// (visitors never see errors: Arch 02). The numbers are the database's; nothing here computes or invents one.
import { cacheLife, cacheTag } from "next/cache";
import type { NextMeetingRow, SeriesLatestRow, SeriesRow, TaskFreshnessRow } from "@/lib/queries/api";
import { readAll } from "@/lib/queries/read";
import { publicClient } from "@/lib/supabase/public";

export type Read<T> = ({ ok: true } & T) | { ok: false };

function failed(what: string, error: unknown): { ok: false } {
  console.error(`[shell] ${what} read failed: ${error instanceof Error ? error.message : String(error)}`);
  return { ok: false };
}

/** The tape's rows: every published series' latest two values, and every published series' source line. */
export async function readTape(): Promise<Read<{ latest: SeriesLatestRow[]; series: SeriesRow[] }>> {
  "use cache";
  cacheLife("minutes");
  cacheTag("shell", "series");
  const db = publicClient();
  if (!db) return failed("tape", "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
  try {
    const [latest, series] = await Promise.all([
      readAll<SeriesLatestRow>("api.series_latest", (from, to) =>
        db
          .from("series_latest")
          .select("series_key, name, unit, frequency, last_date, last_value, prev_date, prev_value, change", { count: "exact" })
          .order("series_key")
          .range(from, to),
      ),
      readAll<SeriesRow>("api.series", (from, to) =>
        db.from("series").select("key, name, market, attribution", { count: "exact" }).order("key").range(from, to),
      ),
    ]);
    return { ok: true, latest, series };
  } catch (error) {
    return failed("tape", error);
  }
}

/** Every scheduled meeting from today on (the database's date), soonest first. */
export async function readNextMeetings(): Promise<Read<{ meetings: NextMeetingRow[] }>> {
  "use cache";
  cacheLife("minutes");
  cacheTag("shell", "meetings");
  const db = publicClient();
  if (!db) return failed("next meetings", "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
  try {
    const meetings = await readAll<NextMeetingRow>("api.next_meetings", (from, to) =>
      db
        .from("next_meetings")
        .select("bank, slug, decision_date, decision_at", { count: "exact" })
        .order("decision_date")
        .order("slug")
        .range(from, to),
    );
    return { ok: true, meetings };
  } catch (error) {
    return failed("next meetings", error);
  }
}

/** Every task's freshness row (the view turns a task stale by itself: F17). */
export async function readFreshness(): Promise<Read<{ tasks: TaskFreshnessRow[]; readAt: string }>> {
  "use cache";
  cacheLife("minutes");
  cacheTag("shell", "freshness");
  const db = publicClient();
  if (!db) return failed("freshness", "NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY isn't set");
  try {
    const tasks = await readAll<TaskFreshnessRow>("api.task_freshness", (from, to) =>
      db
        .from("task_freshness")
        .select("task_key, last_checked_at, last_success_at, status", { count: "exact" })
        .order("task_key")
        .range(from, to),
    );
    // When the rows were read: the checked time's year is worked out against it (Design 01 §3).
    return { ok: true, tasks, readAt: new Date().toISOString() };
  } catch (error) {
    return failed("freshness", error);
  }
}
