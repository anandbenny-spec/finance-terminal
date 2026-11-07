// web/lib/queries/read.ts: reads a whole list from the Data API, or fails loudly (Review 01, N2; CLAUDE.md §6).
//
// The Data API returns at most 1,000 rows per request (supabase/config.toml `max_rows`) and cuts a longer list
// without an error. So every list query here asks for an exact count, pages with `.range()` until it holds that
// many rows, and throws if it can't: a page never shows a silently shortened list. A1 (#6) brings every chart and
// table query under this module; the shell's three small reads (C2) are the first.

/** The Data API's per-request cap (supabase/config.toml `max_rows`). */
export const MAX_ROWS = 1000;

/** What one page of a PostgREST read resolves to (supabase-js's response, the parts used here). */
export type PageResult<T> = {
  data: T[] | null;
  error: { message: string; code?: string } | null;
  count: number | null;
};

/**
 * Builds the query for rows `from`…`to` (inclusive), with `{ count: "exact" }` and a total order, so pages
 * neither overlap nor skip rows. Called once per page.
 */
export type PageQuery<T> = (from: number, to: number) => PromiseLike<PageResult<T>>;

export class IncompleteReadError extends Error {
  constructor(what: string, detail: string) {
    super(`${what}: ${detail}`);
    this.name = "IncompleteReadError";
  }
}

/** How long to wait before the one retry of a page that hit a statement timeout. */
export const RETRY_DELAY_MS = 300;

/** Postgres code 57014 (query_canceled), or the message its statement timeout carries. */
function isStatementTimeout(error: { message: string; code?: string }): boolean {
  return error.code === "57014" || /canceling statement due to statement timeout/i.test(error.message);
}

/** Every row the query matches, checked against the exact count; throws instead of returning a cut list. */
export async function readAll<T>(what: string, query: PageQuery<T>, pageSize: number = MAX_ROWS): Promise<T[]> {
  if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > MAX_ROWS) {
    throw new RangeError(`readAll: page size must be 1–${MAX_ROWS}, got ${pageSize}`);
  }
  const rows: T[] = [];
  let total: number | null = null;
  do {
    const from = rows.length;
    const started = Date.now();
    let page = await query(from, from + pageSize - 1);
    if (page.error && isStatementTimeout(page.error)) {
      // A cold read on a quiet project can hit the role's 3 s limit (#303): ask for the same page once more.
      console.warn(`readAll: ${what}: statement timeout after ${Date.now() - started} ms, retrying the page once`);
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
      const retryStarted = Date.now();
      page = await query(from, from + pageSize - 1);
      console.warn(`readAll: ${what}: retry ${page.error ? "failed" : "succeeded"} after ${Date.now() - retryStarted} ms`);
    }
    if (page.error) throw new IncompleteReadError(what, page.error.message);
    if (page.count === null || !Number.isInteger(page.count)) {
      throw new IncompleteReadError(what, "the response carried no exact count");
    }
    if (total === null) total = page.count;
    else if (page.count !== total) {
      throw new IncompleteReadError(what, `the count changed between pages (${total}, then ${page.count})`);
    }
    const data = page.data ?? [];
    if (data.length === 0 && rows.length < total) {
      throw new IncompleteReadError(what, `got ${rows.length} of ${total} rows, then an empty page`);
    }
    rows.push(...data);
  } while (rows.length < total);
  if (rows.length !== total) throw new IncompleteReadError(what, `got ${rows.length} rows for a count of ${total}`);
  return rows;
}
