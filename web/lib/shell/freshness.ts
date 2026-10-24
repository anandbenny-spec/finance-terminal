// web/lib/shell/freshness.ts: the status bar's freshness summary (Design 01 §1.8, §4.1; Review 01, F17). Pure.
//
// - Counts only the tasks behind something live (lib/feeds.ts). Visitors see a count and a word, never task
//   names, errors or run ids (Arch 02's public/private split).
// - api.task_freshness turns a task "stale" by itself once its runs stop (F17). A task whose last run failed is
//   "delayed" (the last good data stays: "Update delayed"). A task with no freshness row, or none that ever
//   succeeded, isn't current either: it counts as stale (fail closed).
// - "Checked" is the oldest of the counted tasks' last successes, so it never claims the data is fresher than its
//   least recent part.
import type { TaskFreshnessRow } from "@/lib/queries/api";
import { formatCheckedTime, formatDate, type Instant } from "@/lib/format";

export type TaskState = "current" | "delayed" | "stale";

export type FreshnessSummary = { state: "current" | "behind"; text: string; delayed: number; stale: number };

/** One task's state from its row (or its missing row). */
export function taskState(row: TaskFreshnessRow | undefined): TaskState {
  if (!row || !row.last_success_at) return "stale";
  if (row.status === "failed") return "delayed";
  if (row.status === "stale") return "stale";
  return "current";
}

function sources(n: number): string {
  return n === 1 ? "1 source" : `${n} sources`;
}

/** The summary for `tasks` at `now` (the year of the checked time depends on it). */
export function summarise(rows: readonly TaskFreshnessRow[], tasks: readonly string[], now: Instant): FreshnessSummary {
  const byKey = new Map(rows.map((r) => [r.task_key, r]));
  const states = tasks.map((t) => taskState(byKey.get(t)));
  const delayed = states.filter((s) => s === "delayed").length;
  const stale = states.filter((s) => s === "stale").length;
  if (delayed + stale > 0) {
    const parts = [delayed ? `${sources(delayed)} delayed` : "", stale ? `${delayed ? stale : sources(stale)} stale` : ""];
    return { state: "behind", text: parts.filter(Boolean).join(", "), delayed, stale };
  }
  if (tasks.length === 0) return { state: "current", text: "All data current", delayed: 0, stale: 0 };
  const oldest = tasks.map((t) => byKey.get(t)!.last_success_at!).reduce((a, b) => (Date.parse(a) <= Date.parse(b) ? a : b));
  return { state: "current", text: `All data current · checked ${formatCheckedTime(oldest, now)}`, delayed: 0, stale: 0 };
}

/** A panel's footer freshness (Design 01 §1.8, §4.2): the date its data runs to, and when its tasks last succeeded. */
export type PanelFreshness = { text: string; delayed: boolean };

/**
 * "Data to 17 Oct 2026 · checked 20 Oct, 06:41 IST" for a panel fed by `tasks`. "Checked" is the oldest of their
 * last successes (never fresher than the least recent part); `delayed` when any of them isn't current (its last
 * run failed, or its runs stopped), so the panel shows "Update delayed" and keeps its last good data. With no
 * freshness rows read, only the data's own date is claimed.
 */
export function panelFreshness(
  rows: readonly TaskFreshnessRow[] | null,
  tasks: readonly string[],
  dataTo: string | null,
  now: Instant,
): PanelFreshness {
  const parts = dataTo ? [`Data to ${formatDate(dataTo)}`] : [];
  if (rows === null) return { text: parts.join(" · "), delayed: false };
  const byKey = new Map(rows.map((r) => [r.task_key, r]));
  const delayed = tasks.some((t) => taskState(byKey.get(t)) !== "current");
  const successes = tasks.map((t) => byKey.get(t)?.last_success_at).filter((t): t is string => Boolean(t));
  if (successes.length === tasks.length && successes.length > 0) {
    const oldest = successes.reduce((a, b) => (Date.parse(a) <= Date.parse(b) ? a : b));
    parts.push(`checked ${formatCheckedTime(oldest, now)}`);
  }
  return { text: parts.join(" · "), delayed };
}
