"use client";

// RefreshButton (Design 01 §4.7; Spec 01 §4.4; Architecture 02 §1.6): the admin's refresh in a panel header, for
// the tasks that feed the panel (lib/feeds.ts). Admin only: for anyone else it renders nothing, and the API
// refuses anyone else anyway (A3). States: requesting (spinner) · running ("Running · 1:12", "View run ↗") · done
// ("Updated just now") · cooldown (disabled, "Available in 32 min") · rejected (a StateNote with the route's
// reason). A panel fed by one task gets the button itself; a panel fed by several opens a list, one row per task,
// since each refresh starts one task's run (decided by Claude, standing delegation, 24 Oct 2026; issue #23).
// Visitor refresh isn't built (Spec 01 D10).
import { Loader2, RotateCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { StateNote } from "@/components/data/StateNote";
import { useAdmin } from "@/components/shell/admin";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { readHealth, type Health } from "@/lib/admin/api";
import { POLL_MS, initialState, pollRefresh, startRefresh, type RefreshState } from "@/lib/admin/refresh";
import { formatElapsed, formatMinutesUntil, formatRelativeTime } from "@/lib/format";

const nowIso = () => new Date().toISOString();

// One health read per page load, shared by every button on it (the cooldowns); read again after a cooldown answer.
let health: Promise<Health | null> | null = null;
function cooldowns(again = false): Promise<Health | null> {
  if (!health || again) health = readHealth().then((r) => r.health).catch(() => null);
  return health;
}

const ICON_BUTTON =
  "grid size-7 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-active hover:text-ink-1 disabled:pointer-events-none disabled:text-ink-3";

function useTick(ms: number, on: boolean): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!on) return;
    const id = setInterval(() => setN((x) => x + 1), ms);
    return () => clearInterval(id);
  }, [ms, on]);
  return n;
}

/** One task's control. Exported for the multi-task list and the tests. */
export function TaskRefresh({ task, start = startRefresh, poll = pollRefresh }: { task: string; start?: typeof startRefresh; poll?: typeof pollRefresh }) {
  const [state, setState] = useState<RefreshState>({ kind: "idle" });
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    cooldowns().then((h) => {
      if (alive.current) setState((s) => (s.kind === "idle" ? initialState(h?.tasks.find((t) => t.key === task)?.refresh_available_at, nowIso()) : s));
    });
    return () => {
      alive.current = false;
    };
  }, [task]);

  // A running request is checked every POLL_MS until its run finishes.
  useEffect(() => {
    if (state.kind !== "running") return;
    const id = setTimeout(() => {
      poll(state, nowIso).then((next) => alive.current && setState(next));
    }, POLL_MS);
    return () => clearTimeout(id);
  }, [state, poll]);

  useTick(1000, state.kind === "running");
  useTick(30_000, state.kind === "cooldown" || state.kind === "done");

  const click = useCallback(async () => {
    setState({ kind: "requesting" });
    const next = await start(task, nowIso);
    if (!alive.current) return;
    if (next.kind === "cooldown") {
      const h = await cooldowns(true);
      if (!alive.current) return;
      setState({ kind: "cooldown", until: h?.tasks.find((t) => t.key === task)?.refresh_available_at ?? null });
    } else {
      setState(next);
    }
  }, [start, task]);

  const now = nowIso();
  const label = `Refresh ${task}`;
  // A cooldown that has run out shows the plain button again.
  const view: RefreshState = state.kind === "cooldown" && state.until && formatMinutesUntil(state.until, now) === null ? { kind: "idle" } : state;
  switch (view.kind) {
    case "requesting":
      return (
        <span role="status" className="grid size-7 place-items-center text-ink-2">
          <Loader2 aria-hidden className="size-4 animate-spin" />
          <span className="sr-only">Requesting a refresh of {task}</span>
        </span>
      );
    case "running":
      return (
        <span role="status" className="inline-flex items-center gap-2 text-meta text-ink-2">
          <span className="font-mono">Running · {formatElapsed(Math.max(0, Date.parse(now) - Date.parse(view.since)) / 1000)}</span>
          {view.runUrl && (
            <a href={view.runUrl} target="_blank" rel="noreferrer" className="text-ink-2 underline hover:text-ink-1">
              View run ↗
            </a>
          )}
        </span>
      );
    case "done":
      return (
        <span role="status" className="inline-flex items-center gap-1 text-meta text-ink-2">
          Updated {formatRelativeTime(view.at, now)}
          <button type="button" onClick={click} aria-label={label} title={label} className={ICON_BUTTON}>
            <RotateCw aria-hidden className="size-4" />
          </button>
        </span>
      );
    case "cooldown": {
      const left = view.until ? formatMinutesUntil(view.until, now) : null;
      const tip = left ? `Available in ${left}` : "Refreshed in the last 45 minutes: available later";
      return (
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} aria-label={`${label}: ${tip}`} className="inline-flex rounded-md">
              <button type="button" disabled aria-hidden tabIndex={-1} className={ICON_BUTTON}>
                <RotateCw aria-hidden className="size-4" />
              </button>
            </span>
          </TooltipTrigger>
          <TooltipContent>{tip}</TooltipContent>
        </Tooltip>
      );
    }
    case "rejected":
      return (
        <span className="inline-flex items-center gap-1 text-meta">
          <StateNote kind="error">{view.reason}</StateNote>
          <button type="button" onClick={click} aria-label={`Try again: ${label}`} title="Try again" className={ICON_BUTTON}>
            <RotateCw aria-hidden className="size-4" />
          </button>
        </span>
      );
    default:
      return (
        <button type="button" onClick={click} aria-label={label} title={label} className={ICON_BUTTON}>
          <RotateCw aria-hidden className="size-4" />
        </button>
      );
  }
}

/** The view, given whether the admin is looking (exported for the tests). */
export function RefreshControl({ tasks, isAdmin }: { tasks: readonly string[]; isAdmin: boolean }) {
  if (!isAdmin || tasks.length === 0) return null;
  if (tasks.length === 1) return <TaskRefresh task={tasks[0]} />;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" aria-label="Refresh this panel's data" title="Refresh this panel's data" className={ICON_BUTTON}>
          <RotateCw aria-hidden className="size-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-auto min-w-72">
        <p className="mb-2 text-meta text-ink-2">Refresh a task behind this panel (admin)</p>
        <ul className="flex flex-col divide-y divide-line">
          {tasks.map((t) => (
            <li key={t} className="flex min-h-9 items-center justify-between gap-3">
              <span className="font-mono text-meta text-ink-1">{t}</span>
              <TaskRefresh task={t} />
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

/** In a panel header: the admin's refresh for the panel's feeding tasks; nothing for a visitor. */
export function RefreshButton({ tasks }: { tasks: readonly string[] }) {
  const { isAdmin } = useAdmin();
  return <RefreshControl tasks={tasks} isAdmin={isAdmin} />;
}
