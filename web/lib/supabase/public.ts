// web/lib/supabase/public.ts: the Data API client pages read published data with (CLAUDE.md §2, §4).
//
// It holds the browser key only (NEXT_PUBLIC_SUPABASE_ANON_KEY: the anon key, or the newer publishable key),
// which is public by design: the Data API exposes the `api` schema alone and RLS decides what a visitor sees.
// Server components use it to render published data; it never holds the service-role key and never logs in.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { ApiDatabase } from "@/lib/queries/api";

export type PublicClient = SupabaseClient<ApiDatabase, "api">;

/** The public URL and browser key, or null when this build has none (CI's build, a fresh clone). */
export function publicConfig(): { url: string; key: string } | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  return url && key ? { url, key } : null;
}

let requestSeq = 0;

/**
 * A fetch that makes every request distinct (#309). Next.js memoizes identical GETs inside one server render, so
 * readAll's retry of a timed-out page was answered from the first attempt's failed response, not the database.
 * A harmless header that differs per request keeps the memo from matching; the URL, key and query are unchanged.
 */
export const distinctFetch: typeof fetch = (input, init) => {
  const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
  headers.set("x-ft-attempt", Date.now().toString(36) + "-" + (requestSeq++).toString(36));
  return fetch(input, Object.assign({}, init, { headers }));
};

/** A client on the `api` schema with the browser key, holding no session (nothing to persist on the server). */
export function publicClient(): PublicClient | null {
  const config = publicConfig();
  if (!config) return null;
  return createClient<ApiDatabase, "api">(config.url, config.key, {
    db: { schema: "api" },
    global: { fetch: distinctFetch },
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}
