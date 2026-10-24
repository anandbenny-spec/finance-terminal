// The admin's pieces rendered as the server sends them (C6): the refresh control is absent for a visitor, and the
// health page's tables say every status in words and never come out blank.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HealthTables } from "@/components/admin/HealthView";
import { RefreshControl } from "@/components/admin/RefreshButton";
import { Panel } from "@/components/data/Panel";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Health } from "@/lib/admin/api";

const html = (node: ReactNode) => renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

describe("RefreshControl", () => {
  it("renders nothing for a visitor, or with no tasks", () => {
    expect(html(<RefreshControl tasks={["policy.calendar"]} isAdmin={false} />)).toBe("");
    expect(html(<RefreshControl tasks={[]} isAdmin />)).toBe("");
  });

  it("gives the admin one button for a one-task panel, and a list for a panel with several", () => {
    expect(html(<RefreshControl tasks={["policy.calendar"]} isAdmin />)).toContain('aria-label="Refresh policy.calendar"');
    expect(html(<RefreshControl tasks={["policy.fed_documents", "policy.ai_batch"]} isAdmin />)).toContain('aria-label="Refresh this panel&#x27;s data"');
  });

  it("a visitor's panel header has no refresh button", () => {
    const out = html(
      <Panel title="Latest Fed decision" titleId="p" refresh={["policy.fed_documents"]}>
        body
      </Panel>,
    );
    expect(out).not.toContain("Refresh");
  });
});

const HEALTH: Health = {
  as_of: "2026-10-24T05:00:00+00:00",
  open_alert_count: 1,
  tasks: [
    { key: "policy.calendar", module: "policy", description: "d", enabled: true, uses_nse: false, workflows: ["weekly"], due_rules: { weekly: "every 25 days" }, last_status: "success", last_run_at: "2026-10-23T04:00:00+00:00", last_error: null, last_success_at: "2026-10-23T04:01:00+00:00", next_due_at: "2026-11-17T04:01:00+00:00", is_overdue: false, open_alerts: 0, refresh_available_at: null },
    { key: "policy.rbi_documents", module: "policy", description: "d", enabled: true, uses_nse: false, workflows: ["daily"], due_rules: { daily: "every 20 hours" }, last_status: "checks_failed", last_run_at: "2026-10-24T01:07:00+00:00", last_error: "repo rate not parsed", last_success_at: "2026-10-22T01:07:00+00:00", next_due_at: "2026-10-22T21:07:00+00:00", is_overdue: false, open_alerts: 1, refresh_available_at: null },
    { key: "policy.reactions", module: "policy", description: "Pass 1b", enabled: false, uses_nse: false, workflows: ["daily"], due_rules: {}, last_status: null, last_run_at: null, last_error: null, last_success_at: null, next_due_at: null, is_overdue: false, open_alerts: 0, refresh_available_at: null },
  ],
  alerts: [{ id: 3, task_key: "policy.rbi_documents", severity: "error", message: "rbi_resolution_failed:mpc-2026-12-04", dedupe_key: "x", raised_at: "2026-10-24T01:08:00+00:00" }],
  runs: [{ id: 9, workflow: "daily", trigger: "schedule", runner: "github", github_run_id: 123, started_at: "2026-10-24T01:07:00+00:00", finished_at: "2026-10-24T01:10:00+00:00", status: "partial", tasks: [{ task_key: "policy.rbi_documents", status: "checks_failed", rows_written: 0, started_at: "", finished_at: null, error: "repo rate not parsed" }] }],
};

describe("HealthTables", () => {
  it("shows each task's status in words, failures with their error, and problems first", () => {
    const t = text(html(<HealthTables health={HEALTH} />));
    expect(t).toMatch(/policy\.rbi_documents daily: every 20 hours .* Failed repo rate not parsed/);
    expect(t.indexOf("policy.rbi_documents")).toBeLessThan(t.indexOf("policy.calendar"));
    expect(t).toContain("Disabled");
    expect(t).toContain("23 Oct, 09:31 IST");
    expect(t).toContain("Due now");
  });

  it("lists open alerts and recent runs, with the run's link", () => {
    const out = html(<HealthTables health={HEALTH} />);
    expect(text(out)).toContain("Error policy.rbi_documents rbi_resolution_failed:mpc-2026-12-04");
    expect(out).toContain('href="https://github.com/anandbenny-spec/finance-terminal/actions/runs/123"');
    expect(text(out)).toContain("daily (schedule) partial policy.rbi_documents: checks failed");
    expect(text(out)).toContain("Source: this site's run log (ops)");
  });

  it("is never blank", () => {
    const t = text(html(<HealthTables health={{ ...HEALTH, open_alert_count: 0, tasks: [], alerts: [], runs: [] }} />));
    expect(t).toContain("No tasks are registered yet");
    expect(t).toContain("No open alerts.");
    expect(t).toContain("No runs yet.");
  });
});
