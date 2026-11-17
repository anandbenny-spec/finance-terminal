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

/**
 * The most Data API requests one server instance has in flight at once (#620). On 17 Nov 2026 eight cold Calls tabs loaded
 * together on production timed out (api.call_figures past the visitor role's 3 s on every tab, each read 30–47 ms on dev), while
 * one cold tab alone loaded in 4.2 s: the free-plan database takes only a few statements at once, and each burst of renders sent
 * it many (each page's existence, title, header and freshness reads at once, then each tab's). Requests over the cap wait here,
 * in the server, where no statement timeout runs, and go out in the order they came.
 */
export const MAX_IN_FLIGHT = 2;
let inFlight = 0;
const waiting: Array<() => void> = [];

/** Takes a slot, or waits for one; a released slot is handed straight to the first waiter. */
async function acquire(): Promise<void> {
  if (inFlight < MAX_IN_FLIGHT) {
    inFlight++;
    return;
  }
  await new Promise<void>((resolve) => waiting.push(resolve));
}

function release(): void {
  const next = waiting.shift();
  if (next) next();
  else inFlight--;
}

/** For the tests: the requests in flight and waiting now. */
export function limiterState(): { inFlight: number; waiting: number } {
  return { inFlight, waiting: waiting.length };
}

/**
 * distinctFetch under the cap (#620). The slot is held until the body has arrived, so a request counts until the database's
 * answer is complete, and is released whether the request succeeds or fails.
 */
export const limitedFetch: typeof fetch = async (input, init) => {
  await acquire();
  try {
    const res = await distinctFetch(input, init);
    const body = await res.arrayBuffer();
    return new Response(body, { status: res.status, statusText: res.statusText, headers: res.headers });
  } finally {
    release();
  }
};

/** A client on the `api` schema with the browser key, holding no session (nothing to persist on the server). */
export function publicClient(): PublicClient | null {
  const config = publicConfig();
  if (!config) return null;
  return createClient<ApiDatabase, "api">(config.url, config.key, {
    db: { schema: "api" },
    global: { fetch: limitedFetch },
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}
