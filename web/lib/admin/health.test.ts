// The health page's status words and run lines (C6; Design 01 §4.7).
import { describe, expect, it } from "vitest";
import { runLink, runTasksLine, taskStatus } from "@/lib/admin/health";

const base = { enabled: true, last_status: "success", is_overdue: false, last_success_at: "2026-10-24T01:00:00Z" };

describe("taskStatus", () => {
  it("names each state in words", () => {
    expect(taskStatus(base)).toBe("OK");
    expect(taskStatus({ ...base, is_overdue: true })).toBe("Delayed");
    expect(taskStatus({ ...base, last_status: "checks_failed" })).toBe("Failed");
    expect(taskStatus({ ...base, last_status: "failed", is_overdue: true })).toBe("Failed");
    expect(taskStatus({ ...base, last_status: null, last_success_at: null, is_overdue: true })).toBe("Never run");
    expect(taskStatus({ ...base, enabled: false, last_status: null, last_success_at: null, is_overdue: false })).toBe("Disabled");
  });
});

describe("runTasksLine and runLink", () => {
  it("lists what each task did", () => {
    expect(runTasksLine({ tasks: [] })).toBe("No task was due");
    expect(
      runTasksLine({
        tasks: [
          { task_key: "policy.calendar", status: "success", rows_written: 1234, started_at: "", finished_at: null, error: null },
          { task_key: "policy.rbi_documents", status: "checks_failed", rows_written: 0, started_at: "", finished_at: null, error: "x" },
          { task_key: "policy.fed_documents", status: "success", rows_written: 1, started_at: "", finished_at: null, error: null },
        ],
      }),
    ).toBe("policy.calendar: success, 1,234 rows · policy.rbi_documents: checks failed · policy.fed_documents: success, 1 row");
  });

  it("links a GitHub run, and nothing for a local one", () => {
    expect(runLink(42)).toBe("https://github.com/anandbenny-spec/finance-terminal/actions/runs/42");
    expect(runLink(null)).toBeNull();
  });
});
