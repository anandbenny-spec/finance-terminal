"use client";

// /admin/health (C6; Spec 01 §4.4; Design 01 §4.7; Architecture 02 §1.4): the tasks' health, the open alerts
// (oldest first) and the recent runs, read through /api/admin/health (ops is invisible to the browser key). Admin
// only: for anyone else the page asks them to log in, and the API refuses them anyway. Errors and task names are
// fine here (the admin's page); visitors never see them (Design 01 §1.8). Status is icon and word, never colour alone.
import { CircleAlert, CircleCheck, CircleMinus, Clock, Info, RotateCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { TaskRefresh } from "@/components/admin/RefreshButton";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { Section } from "@/components/data/SectionHeading";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { useAdmin } from "@/components/shell/admin";
import { readHealth, type Health, type HealthAlert, type HealthRun, type HealthTask } from "@/lib/admin/api";
import { STATUS_RANK, runLink, runTasksLine, taskStatus, type HealthStatus } from "@/lib/admin/health";
import { NOT_AVAILABLE, formatCheckedTime, formatCount } from "@/lib/format";

const SOURCE = "Source: this site's run log (ops)";

const STATUS_ICON: Record<HealthStatus, { Icon: typeof CircleCheck; className: string }> = {
  OK: { Icon: CircleCheck, className: "text-ok" },
  Delayed: { Icon: Clock, className: "text-warn" },
  Failed: { Icon: CircleAlert, className: "text-error" },
  "Never run": { Icon: Clock, className: "text-warn" },
  Disabled: { Icon: CircleMinus, className: "text-ink-3" },
};

export function StatusWord({ status }: { status: HealthStatus }) {
  const { Icon, className } = STATUS_ICON[status];
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon aria-hidden className={`size-3.5 shrink-0 ${className}`} />
      {status}
    </span>
  );
}

const SEVERITY: Record<HealthAlert["severity"], { Icon: typeof Info; className: string; word: string }> = {
  info: { Icon: Info, className: "text-ink-3", word: "Info" },
  warn: { Icon: Clock, className: "text-warn", word: "Warning" },
  error: { Icon: CircleAlert, className: "text-error", word: "Error" },
};

const when = (t: string | null, now: string) => (t ? formatCheckedTime(t, now) : NOT_AVAILABLE);

function nextDue(t: HealthTask, now: string): string {
  if (!t.enabled) return NOT_AVAILABLE;
  if (!t.next_due_at || Date.parse(t.next_due_at) <= Date.parse(now)) return "Due now";
  return formatCheckedTime(t.next_due_at, now);
}

/** The page's tables, given the data (exported for the tests). */
export function HealthTables({ health }: { health: Health }) {
  const now = health.as_of;
  const footer = (
    <>
      <SourceLine lines={[SOURCE]} />
      <span className="font-mono">Read {formatCheckedTime(now, now)}</span>
    </>
  );
  const taskCols: Column<HealthTask>[] = [
    {
      id: "task",
      header: "Task",
      cell: (t) => (
        <span className="flex flex-col">
          <span className="font-mono text-ink-1">{t.key}</span>
          <span className="text-meta text-ink-3">{Object.entries(t.due_rules).map(([w, r]) => `${w}: ${r}`).join(" · ") || t.description}</span>
        </span>
      ),
      sortValue: (t) => t.key,
    },
    { id: "success", header: "Last success", cell: (t) => when(t.last_success_at, now), sortValue: (t) => t.last_success_at ?? "" },
    {
      id: "status",
      header: "Status",
      cell: (t) => (
        <span className="flex flex-col">
          <StatusWord status={taskStatus(t)} />
          {taskStatus(t) === "Failed" && t.last_error && <span className="max-w-96 text-meta break-words text-ink-3">{t.last_error}</span>}
        </span>
      ),
      sortValue: (t) => STATUS_RANK[taskStatus(t)],
    },
    { id: "due", header: "Next due", cell: (t) => nextDue(t, now), sortValue: (t) => t.next_due_at ?? "" },
    { id: "alerts", header: "Open alerts", align: "right", cell: (t) => formatCount(t.open_alerts), sortValue: (t) => t.open_alerts },
    { id: "refresh", header: "Refresh", cell: (t) => (t.enabled ? <TaskRefresh task={t.key} /> : NOT_AVAILABLE) },
  ];
  const alertCols: Column<HealthAlert>[] = [
    { id: "raised", header: "Raised", cell: (a) => when(a.raised_at, now) },
    {
      id: "severity",
      header: "Severity",
      cell: (a) => {
        const { Icon, className, word } = SEVERITY[a.severity];
        return (
          <span className="inline-flex items-center gap-1.5">
            <Icon aria-hidden className={`size-3.5 shrink-0 ${className}`} />
            {word}
          </span>
        );
      },
    },
    { id: "task", header: "Task", cell: (a) => <span className="font-mono">{a.task_key ?? NOT_AVAILABLE}</span> },
    { id: "message", header: "Alert", cell: (a) => <span className="break-words">{a.message}</span> },
  ];
  const runCols: Column<HealthRun>[] = [
    { id: "started", header: "Started", cell: (r) => when(r.started_at, now) },
    { id: "workflow", header: "Workflow", cell: (r) => `${r.workflow} (${r.trigger})` },
    { id: "status", header: "Status", cell: (r) => r.status },
    { id: "tasks", header: "Tasks", cell: (r) => <span className="text-meta break-words">{runTasksLine(r)}</span> },
    {
      id: "run",
      header: "Run",
      cell: (r) => {
        const href = runLink(r.github_run_id);
        return href ? (
          <a href={href} target="_blank" rel="noreferrer" className="underline">
            View run ↗
          </a>
        ) : (
          r.runner
        );
      },
    },
  ];
  const shownAlerts = health.alerts.length;
  return (
    <>
      <Section id="tasks" title="Tasks">
        <PanelGrid>
          <Panel title="Task health" titleId="panel-tasks" variant="flush" subtitle="Is every task running on time, and did its last run pass its checks?" footer={footer}>
            <DataTable
              columns={taskCols}
              rows={health.tasks}
              rowKey={(t) => t.key}
              caption="Task health"
              initialSort={{ id: "status", desc: false }}
              empty={<StateNote kind="empty">No tasks are registered yet: the first run adds them.</StateNote>}
            />
          </Panel>
        </PanelGrid>
      </Section>
      <Section
        id="alerts"
        title="Open alerts"
        note={health.open_alert_count > shownAlerts ? `The oldest ${formatCount(shownAlerts)} of ${formatCount(health.open_alert_count)}` : undefined}
      >
        <PanelGrid>
          <Panel title="Open alerts, oldest first" titleId="panel-alerts" variant="flush" subtitle="What needs looking at?" footer={footer}>
            <DataTable columns={alertCols} rows={health.alerts} rowKey={(a) => String(a.id)} caption="Open alerts, oldest first" empty={<StateNote kind="empty">No open alerts.</StateNote>} />
          </Panel>
        </PanelGrid>
      </Section>
      <Section id="runs" title="Recent runs">
        <PanelGrid>
          <Panel title="The last 20 runs" titleId="panel-runs" variant="flush" subtitle="What ran, and how did it end?" footer={footer}>
            <DataTable columns={runCols} rows={health.runs} rowKey={(r) => String(r.id)} caption="The last 20 runs" empty={<StateNote kind="empty">No runs yet.</StateNote>} />
          </Panel>
        </PanelGrid>
      </Section>
    </>
  );
}

export function HealthView() {
  const { isAdmin, ready } = useAdmin();
  const [health, setHealth] = useState<Health | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const apply = useCallback((r: { health: Health | null; status: number }) => {
    setHealth(r.health);
    setStatus(r.status);
    setLoading(false);
  }, []);
  const load = useCallback(() => {
    setLoading(true);
    void readHealth().then(apply);
  }, [apply]);

  useEffect(() => {
    if (!ready || !isAdmin) return;
    let alive = true;
    void readHealth().then((r) => alive && apply(r));
    return () => {
      alive = false;
    };
  }, [ready, isAdmin, apply]);

  if (!ready) return <StateNote kind="empty">Checking your session…</StateNote>;
  if (!isAdmin)
    return (
      <StateNote kind="unavailable">
        This page is for the admin.{" "}
        <Link href="/admin" className="text-ink-1 underline">
          Log in
        </Link>
      </StateNote>
    );
  return (
    <div className="flex flex-col gap-3">
      <div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-input px-3 text-ui text-ink-1 hover:bg-active disabled:opacity-50"
        >
          <RotateCw aria-hidden className="size-4" />
          {loading ? "Reading…" : "Read again"}
        </button>
      </div>
      {health ? (
        <HealthTables health={health} />
      ) : loading || status === null ? (
        <StateNote kind="empty">Reading the run log…</StateNote>
      ) : status === 401 || status === 403 ? (
        <StateNote kind="error">Your admin session has ended. Log in again.</StateNote>
      ) : (
        <StateNote kind="error">Couldn&apos;t read the run log. Try again.</StateNote>
      )}
    </div>
  );
}
