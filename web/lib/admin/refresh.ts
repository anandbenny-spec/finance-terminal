// web/lib/admin/refresh.ts: what the admin's RefreshButton shows (C6; Design 01 §4.7; Architecture 02 §1.6).
//
// States: requesting (spinner) · running ("Running · 1:12", "View run ↗") · done ("Updated just now") · cooldown
// (disabled, "Available in 32 min") · rejected (a StateNote with the route's reason). These functions turn the
// route's answers into a state; RefreshButton holds it and polls. The reasons are for the admin only: the button
// never renders for a visitor.
import { adminFetch, type AdminDeps, type RefreshStarted, type RefreshStatus } from "@/lib/admin/api";

export type RefreshState =
  | { kind: "idle" }
  | { kind: "requesting" }
  | { kind: "running"; requestId: number; runUrl: string | null; since: string }
  | { kind: "done"; at: string }
  | { kind: "cooldown"; until: string | null }
  | { kind: "rejected"; reason: string };

/** How often a running request is checked, and when the button stops waiting. */
export const POLL_MS = 10_000;
export const GIVE_UP_MS = 75 * 60_000; // longer than any refresh workflow's time limit

/** The state after `POST /api/admin/refresh` answered (`now`: when it answered). */
export function afterStart(status: number, body: unknown, now: string): RefreshState {
  const b = (body ?? {}) as Partial<RefreshStarted>;
  if (status === 202 && typeof b.request_id === "number") {
    return { kind: "running", requestId: b.request_id, runUrl: b.run_url ?? null, since: now };
  }
  if (status === 429 && b.status === "rejected_cooldown") return { kind: "cooldown", until: null };
  if (status === 429 && b.status === "rejected_nse") {
    return { kind: "rejected", reason: "This NSE task ran too recently: it keeps its minimum interval." };
  }
  if (status === 409) return { kind: "rejected", reason: "This task is turned off in tasks.yaml." };
  if (status === 404) return { kind: "rejected", reason: "This task doesn't exist." };
  if (status === 502) return { kind: "rejected", reason: "GitHub didn't start the run. Try again." };
  if (status === 401 || status === 403) return { kind: "rejected", reason: "Your admin session has ended. Log in again." };
  return { kind: "rejected", reason: "Couldn't reach the refresh service. Try again." };
}

/**
 * The state after a status check of a running request. Running until its job run has finished: `success` or
 * `partial` (the run went through; this task's own result is on the health page) is done; `failed` is rejected.
 */
export function afterPoll(state: Extract<RefreshState, { kind: "running" }>, status: number, body: unknown, now: string): RefreshState {
  if (status === 401 || status === 403) return { kind: "rejected", reason: "Your admin session has ended. Log in again." };
  const b = (status === 200 ? body : null) as Partial<RefreshStatus> | null;
  const runUrl = b?.run_url ?? state.runUrl;
  if (b?.status === "failed") return { kind: "rejected", reason: "GitHub didn't start the run. Try again." };
  const job = b?.job_run ?? null;
  if (job?.status === "success" || job?.status === "partial") return { kind: "done", at: job.finished_at ?? now };
  if (job?.status === "failed") return { kind: "rejected", reason: "The run failed: see the health page." };
  if (Date.parse(now) - Date.parse(state.since) > GIVE_UP_MS) {
    return { kind: "rejected", reason: "No result after 75 minutes: check the run." };
  }
  return { ...state, runUrl };
}

/** The state a task starts in, from the health page's cooldown time. */
export function initialState(refreshAvailableAt: string | null | undefined, now: string): RefreshState {
  return refreshAvailableAt && Date.parse(refreshAvailableAt) > Date.parse(now)
    ? { kind: "cooldown", until: refreshAvailableAt }
    : { kind: "idle" };
}

export async function startRefresh(task: string, now: () => string, deps?: AdminDeps): Promise<RefreshState> {
  const r = await adminFetch<RefreshStarted>("/api/admin/refresh", { method: "POST", json: { task } }, deps);
  return afterStart(r.status, r.body, now());
}

export async function pollRefresh(state: Extract<RefreshState, { kind: "running" }>, now: () => string, deps?: AdminDeps): Promise<RefreshState> {
  const r = await adminFetch<RefreshStatus>(`/api/admin/refresh/${state.requestId}`, {}, deps);
  return afterPoll(state, r.status, r.body, now());
}
