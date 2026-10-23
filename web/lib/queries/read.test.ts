// readAll: a list read either returns every row the database holds or throws (Review 01, N2). The mocked client
// cuts every response at `cap` rows, as the Data API's max_rows does, without an error.
import { describe, expect, it } from "vitest";
import { IncompleteReadError, MAX_ROWS, readAll, type PageQuery, type PageResult } from "./read";

function table(n: number) {
  return Array.from({ length: n }, (_, i) => ({ id: i }));
}

/** A Data API stand-in: answers ranges from `rows`, cut at `cap` rows, with the exact count. */
function api(rows: { id: number }[], { cap = MAX_ROWS, count = rows.length } = {}) {
  const calls: [number, number][] = [];
  const query: PageQuery<{ id: number }> = async (from, to) => {
    calls.push([from, to]);
    const page = rows.slice(from, Math.min(to + 1, from + cap));
    return { data: page, error: null, count } satisfies PageResult<{ id: number }>;
  };
  return { query, calls };
}

describe("readAll", () => {
  it("reads a short list in one request", async () => {
    const { query, calls } = api(table(19));
    expect(await readAll("series_latest", query)).toEqual(table(19));
    expect(calls).toEqual([[0, 999]]);
  });

  it("pages past the 1,000-row cut until it holds every row", async () => {
    const { query, calls } = api(table(2850));
    const rows = await readAll("observations", query);
    expect(rows).toHaveLength(2850);
    expect(rows.map((r) => r.id)).toEqual(table(2850).map((r) => r.id));
    expect(calls).toEqual([
      [0, 999],
      [1000, 1999],
      [2000, 2999],
    ]);
  });

  it("pages when the server cuts below the page size asked for", async () => {
    const { query } = api(table(2500), { cap: 300 });
    expect(await readAll("observations", query)).toHaveLength(2500);
  });

  it("reads an empty list", async () => {
    const { query } = api([]);
    expect(await readAll("next_meetings", query)).toEqual([]);
  });

  it("throws, rather than returning a cut list, when rows stop before the count", async () => {
    const { query } = api(table(1000), { count: 1200 });
    await expect(readAll("observations", query)).rejects.toThrow(IncompleteReadError);
    await expect(readAll("observations", query)).rejects.toThrow("got 1000 of 1200 rows");
  });

  it("throws when the response has no exact count", async () => {
    const query: PageQuery<{ id: number }> = async () => ({ data: table(3), error: null, count: null });
    await expect(readAll("series", query)).rejects.toThrow("no exact count");
  });

  it("throws when the count changes between pages", async () => {
    let n = 0;
    const query: PageQuery<{ id: number }> = async (from) => ({
      data: table(1500).slice(from, from + 1000),
      error: null,
      count: n++ === 0 ? 1500 : 1501,
    });
    await expect(readAll("observations", query)).rejects.toThrow("count changed");
  });

  it("throws on an API error", async () => {
    const query: PageQuery<{ id: number }> = async () => ({ data: null, error: { message: "boom" }, count: null });
    await expect(readAll("task_freshness", query)).rejects.toThrow("task_freshness: boom");
  });

  it("refuses a page size above the cap", async () => {
    const { query } = api(table(3));
    await expect(readAll("series", query, 5000)).rejects.toThrow(RangeError);
  });
});
