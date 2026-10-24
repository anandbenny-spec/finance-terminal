// web/lib/admin/api.ts: the admin's calls to /api/admin/* (C6; Prompt 01 A3, C6; Architecture 02 §1.6).
//
// `ops` is invisible to the browser key, so the admin pages read and act through the API service, sending the
// signed-in admin's access token. The API verifies it (signature, expiry, app_metadata.role = "admin") before
// anything runs: what the browser shows is display only. Browser only; never imported by a server component.

/** One task's row on the health page: `api.admin_health()` (migration 0017), from `ops.task_health`. */
export type HealthTask = {
  key: string;
  module: string;
  description: string;
  enabled: boolean;
  uses_nse: boolean;
  workflows: string[];
  due_rules: Record<string, string>;
  last_status: string | null;
  last_run_at: string | null;
  last_error: string | null;
  last_success_at: string | null;
  next_due_at: string | null;
  is_overdue: boolean;
  open_alerts: number;
  /** When the admin may refresh it again (45 minutes after the last dispatched request); null: now. */
  refresh_available_at: string | null;
};

export type HealthAlert = {
  id: number;
  task_key: string | null;
  severity: "info" | "warn" | "error";
  message: string;
  dedupe_key: string | null;
  raised_at: string;
};

export type HealthTaskRun = {
  task_key: string;
  status: string;
  rows_written: number;
  started_at: string;
  finished_at: string | null;
  error: string | null;
};

export type HealthRun = {
  id: number;
  workflow: string;
  trigger: string;
  runner: string;
  github_run_id: number | null;
  started_at: string;
  finished_at: string | null;
  status: string;
  tasks: HealthTaskRun[];
};

export type Health = {
  as_of: string;
  open_alert_count: number;
  tasks: HealthTask[];
  alerts: HealthAlert[];
  runs: HealthRun[];
};

/** `POST /api/admin/refresh`'s answer (202, 409, 429 or 502). */
export type RefreshStarted = {
  request_id: number;
  status: "dispatched" | "rejected_disabled" | "rejected_cooldown" | "rejected_nse" | "failed";
  github_run_id?: number | null;
  run_url?: string | null;
};

/** `GET /api/admin/refresh/{id}`'s answer. */
export type RefreshStatus = {
  request_id: number;
  task: string;
  requested_at: string;
  status: string;
  github_run_id: number | null;
  run_url: string | null;
  job_run: { id: number; status: string; started_at: string; finished_at: string | null } | null;
};

export type AdminResult<T> = { ok: true; status: number; body: T } | { ok: false; status: number; body: unknown };

export type AdminDeps = {
  /** The signed-in session's access token, or null. */
  token: () => Promise<string | null>;
  fetch: typeof fetch;
};

async function sessionToken(): Promise<string | null> {
  const { browserClient } = await import("@/lib/supabase/browser");
  const db = browserClient();
  if (!db) return null;
  const { data } = await db.auth.getSession();
  return data.session?.access_token ?? null;
}

const defaultDeps = (): AdminDeps => ({ token: sessionToken, fetch: (...args) => globalThis.fetch(...args) });

/**
 * Calls an admin route with the session's token. Never throws: no token is a 401 without a request, a network
 * failure is status 0, and a body that isn't JSON is null. `ok` means a 2xx answer.
 */
export async function adminFetch<T>(
  path: `/api/admin/${string}`,
  init: { method?: "GET" | "POST"; json?: unknown } = {},
  deps: AdminDeps = defaultDeps(),
): Promise<AdminResult<T>> {
  let token: string | null;
  try {
    token = await deps.token();
  } catch {
    token = null;
  }
  if (!token) return { ok: false, status: 401, body: null };
  let resp: Response;
  try {
    resp = await deps.fetch(path, {
      method: init.method ?? "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        ...(init.json === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: init.json === undefined ? undefined : JSON.stringify(init.json),
      cache: "no-store",
    });
  } catch {
    return { ok: false, status: 0, body: null };
  }
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    body = null;
  }
  return resp.ok ? { ok: true, status: resp.status, body: body as T } : { ok: false, status: resp.status, body };
}

/** The health page's data, or null when it couldn't be read (with the status, for the message). */
export async function readHealth(deps?: AdminDeps): Promise<{ health: Health | null; status: number }> {
  const r = await adminFetch<Health>("/api/admin/health", {}, deps);
  const h = r.ok ? r.body : null;
  const valid = h !== null && typeof h === "object" && Array.isArray(h.tasks) && Array.isArray(h.alerts) && Array.isArray(h.runs);
  return { health: valid ? h : null, status: valid ? r.status : r.ok ? 502 : r.status };
}
