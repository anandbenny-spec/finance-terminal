// web/lib/supabase/browser.ts: the browser's Supabase client, for the admin's session only (C6 builds the login).
//
// Pages render published data on the server (lib/supabase/public.ts); the browser needs Supabase only for the one
// admin's sign-in. This client holds the same public browser key and keeps its session under our own storage key,
// so the shell can tell, without loading supabase-js for every visitor, whether a session might exist.
//
// Showing the admin group is display only: what a session's token may do is decided server-side, where every
// /api/admin/* route verifies the token's signature and requires app_metadata.role = "admin" (A3; CLAUDE.md §4).
import { createClient, type SupabaseClient, type User } from "@supabase/supabase-js";
import type { ApiDatabase } from "@/lib/queries/api";
import { publicConfig } from "@/lib/supabase/public";

/** Where supabase-js keeps the session in localStorage (set explicitly, so the shell can look for it). */
export const AUTH_STORAGE_KEY = "ft-auth";

let client: SupabaseClient<ApiDatabase, "api"> | null | undefined;

/** The one browser client (null when this build has no Supabase settings). Browser only. */
export function browserClient(): SupabaseClient<ApiDatabase, "api"> | null {
  if (client === undefined) {
    const config = publicConfig();
    client = config
      ? createClient<ApiDatabase, "api">(config.url, config.key, {
          db: { schema: "api" },
          auth: { storageKey: AUTH_STORAGE_KEY, persistSession: true, autoRefreshToken: true },
        })
      : null;
  }
  return client;
}

/** Whether a stored session might exist (no network, no supabase-js). False where storage is blocked. */
export function hasStoredSession(storage: Pick<Storage, "getItem"> | undefined = globalThis.localStorage): boolean {
  try {
    return Boolean(storage?.getItem(AUTH_STORAGE_KEY));
  } catch {
    return false;
  }
}

/** The admin is the user whose app_metadata (which users can't edit) says so; user_metadata never counts. */
export function isAdminUser(user: Pick<User, "app_metadata"> | null | undefined): boolean {
  return user?.app_metadata?.role === "admin";
}
