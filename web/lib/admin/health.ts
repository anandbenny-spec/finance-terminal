// web/lib/admin/health.ts: the health page's rows (C6; Design 01 §4.7; Architecture 02 §1.4).
//
// Status by icon and word, never colour alone: OK, Delayed (no success within `overdue_after`), Failed (the last
// run failed or its blocking checks failed: the last good data stays), plus Never run and Disabled (policy.reactions
// until Pass 1b), so a task that isn't built can't read as broken.
import type { HealthRun, HealthTask } from "@/lib/admin/api";
import { formatCount } from "@/lib/format";

export type HealthStatus = "OK" | "Delayed" | "Failed" | "Never run" | "Disabled";

export function taskStatus(t: Pick<HealthTask, "enabled" | "last_status" | "is_overdue" | "last_success_at">): HealthStatus {
  if (!t.enabled) return "Disabled";
  if (t.last_status === "failed" || t.last_status === "checks_failed") return "Failed";
  if (!t.last_success_at) return "Never run";
  if (t.is_overdue) return "Delayed";
  return "OK";
}

/** Status order for sorting: problems first. */
export const STATUS_RANK: Record<HealthStatus, number> = { Failed: 0, Delayed: 1, "Never run": 2, OK: 3, Disabled: 4 };

/** One line per task run that did something, for the recent-runs table: `policy.calendar: success, 12 rows`. */
export function runTasksLine(run: Pick<HealthRun, "tasks">): string {
  if (run.tasks.length === 0) return "No task was due";
  return run.tasks
    .map((t) => `${t.task_key}: ${t.status.replace(/_/g, " ")}${t.status === "success" ? `, ${formatCount(t.rows_written)} ${t.rows_written === 1 ? "row" : "rows"}` : ""}`)
    .join(" · ");
}

/** The GitHub Actions page of a run, when the run came from GitHub. */
export function runLink(githubRunId: number | null): string | null {
  return githubRunId ? `https://github.com/anandbenny-spec/finance-terminal/actions/runs/${githubRunId}` : null;
}
