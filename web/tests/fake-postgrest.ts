// A stand-in for the Data API (PostgREST) behind a real supabase-js client, for the row-limit tests (A1, #6; N2).
// It serves in-memory rows per view and behaves like the hosted project where it matters here: at most `maxRows`
// rows per request (supabase/config.toml `max_rows` = 1,000), cut WITHOUT an error, with the exact count in
// Content-Range only when the request asks for it (`Prefer: count=exact`). It understands the parts of the query
// string the page queries use: select (columns, checked), eq/lt/lte/gt/gte/in filters, order, offset and limit.
import { createClient } from "@supabase/supabase-js";
import type { ApiDatabase } from "@/lib/queries/api";

export type Row = Record<string, string | number | boolean | null>;
export type Store = Record<string, Row[]>;
export type SeenRequest = { view: string; params: URLSearchParams; countExact: boolean; returned: number };

const OPS = ["eq", "lt", "lte", "gt", "gte", "in"] as const;
type Op = (typeof OPS)[number];
const RESERVED = new Set(["select", "order", "offset", "limit"]);

function compare(a: Row[string], b: Row[string]): number {
  if (a === b) return 0;
  if (a === null) return 1; // nulls last, as PostgREST's default ascending order
  if (b === null) return -1;
  return a < b ? -1 : 1;
}

function matches(value: Row[string], op: Op, arg: string): boolean {
  if (op === "in") {
    const list = arg.replace(/^\(|\)$/g, "").split(",").map((v) => v.replace(/^"|"$/g, ""));
    return list.includes(String(value));
  }
  if (op === "eq") return String(value) === arg;
  if (value === null) return false;
  const v = typeof value === "number" ? value : String(value);
  const x = typeof value === "number" ? Number(arg) : arg;
  if (op === "lt") return v < x;
  if (op === "lte") return v <= x;
  if (op === "gt") return v > x;
  return v >= x;
}

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

/** A fetch that answers `/rest/v1/<view>` GETs from `store`, cutting every response at `maxRows`. */
export function fakePostgrest(store: Store, maxRows = 1000) {
  const seen: SeenRequest[] = [];
  const fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    const headers = new Headers(init?.headers);
    const view = url.pathname.replace(/^\/rest\/v1\//, "");
    if ((init?.method ?? "GET") !== "GET") return json(405, { message: "read-only fake" });
    if (headers.get("accept-profile") !== "api") return json(406, { message: "only the api schema is exposed" });
    const table = store[view];
    if (!table) return json(404, { code: "42P01", message: `relation "api.${view}" does not exist` });

    const columns = (url.searchParams.get("select") ?? "*").split(",").map((c) => c.trim());
    const known = new Set(Object.keys(table[0] ?? {}));
    const unknown = columns.filter((c) => c !== "*" && known.size > 0 && !known.has(c));
    if (unknown.length) return json(400, { code: "42703", message: `column ${view}.${unknown[0]} does not exist` });

    let rows = table.slice();
    for (const [key, raw] of url.searchParams) {
      if (RESERVED.has(key)) continue;
      const dot = raw.indexOf(".");
      const op = raw.slice(0, dot) as Op;
      if (!OPS.includes(op)) return json(400, { message: `unsupported filter ${key}=${raw}` });
      rows = rows.filter((r) => matches(r[key], op, raw.slice(dot + 1)));
    }
    const order = url.searchParams.get("order");
    if (order) {
      const keys = order.split(",").map((part) => {
        const [col, dir] = part.split(".");
        return { col, desc: dir === "desc" };
      });
      rows.sort((a, b) => {
        for (const { col, desc } of keys) {
          const c = compare(a[col], b[col]);
          if (c !== 0) return desc ? -c : c;
        }
        return 0;
      });
    }
    const total = rows.length;
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = url.searchParams.has("limit") ? Number(url.searchParams.get("limit")) : Infinity;
    const page = rows.slice(offset, offset + Math.min(limit, maxRows)); // the silent cut
    const body = page.map((r) =>
      columns.includes("*") ? r : Object.fromEntries(columns.map((c) => [c, r[c] ?? null])),
    );
    const countExact = /count=exact/.test(headers.get("prefer") ?? "");
    seen.push({ view, params: url.searchParams, countExact, returned: body.length });
    const span = body.length ? `${offset}-${offset + body.length - 1}` : "*";
    return json(200, body, { "content-range": `${span}/${countExact ? total : "*"}` });
  };
  return { fetch, seen };
}

/** A real supabase-js client on the `api` schema whose requests go to `fakePostgrest(store)`. */
export function fakeClient(store: Store, maxRows = 1000) {
  const fake = fakePostgrest(store, maxRows);
  const db = createClient<ApiDatabase, "api">("http://fake.local", "anon-key-for-tests", {
    db: { schema: "api" },
    auth: { persistSession: false, autoRefreshToken: false },
    global: { fetch: fake.fetch },
  });
  return { db, seen: fake.seen };
}
