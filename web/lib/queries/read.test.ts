// readAll: a list read either returns every row the database holds or throws (Review 01, N2). The mocked client
// cuts every response at `cap` rows, as the Data API's max_rows does, without an error.
import { describe, expect, it, vi } from "vitest";
import { IncompleteReadError, MAX_ROWS, RETRY_DELAY_MS, RETRY_JITTER_MS, readAll, retryDelayMs, type PageQuery, type PageResult } from "./read";

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

  describe("statement timeouts (#303)", () => {
    const timeout = { message: "canceling statement due to statement timeout", code: "57014" };

    it("retries a page once after a statement timeout and returns the rows", async () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      const calls: [number, number][] = [];
      let n = 0;
      const query: PageQuery<{ id: number }> = async (from, to) => {
        calls.push([from, to]);
        if (n++ === 0) return { data: null, error: timeout, count: null };
        return { data: table(5), error: null, count: 5 };
      };
      expect(await readAll("company_prices", query)).toEqual(table(5));
      expect(calls).toEqual([[0, 999], [0, 999]]);
      expect(warn).toHaveBeenCalledTimes(2);
      expect(warn.mock.calls[0][0]).toContain("company_prices");
      expect(warn.mock.calls[1][0]).toContain("retry succeeded");
      warn.mockRestore();
    });

    it("recognises a timeout by its message alone", async () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      let n = 0;
      const query: PageQuery<{ id: number }> = async () =>
        n++ === 0
          ? { data: null, error: { message: "canceling statement due to statement timeout" }, count: null }
          : { data: table(2), error: null, count: 2 };
      expect(await readAll("rate_decision_moves", query)).toHaveLength(2);
      warn.mockRestore();
    });

    it("throws after a second timeout", async () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      let calls = 0;
      const query: PageQuery<{ id: number }> = async () => {
        calls++;
        return { data: null, error: timeout, count: null };
      };
      await expect(readAll("company_prices", query)).rejects.toThrow(IncompleteReadError);
      expect(calls).toBe(2);
      warn.mockRestore();
    });

    it("waits 300 ms plus a random share of 900 ms before the retry, so reads that timed out together don't retry together (#516)", () => {
      expect(retryDelayMs(() => 0)).toBe(RETRY_DELAY_MS);
      expect(retryDelayMs(() => 1)).toBe(RETRY_DELAY_MS + RETRY_JITTER_MS);
      expect(retryDelayMs(() => 0.5)).toBe(750);
      expect(retryDelayMs(() => -3)).toBe(300);
      expect(retryDelayMs(() => 7)).toBe(1200);
      const waits = new Set(Array.from({ length: 50 }, () => retryDelayMs()));
      expect(waits.size).toBeGreaterThan(1);
      for (const w of waits) expect(w >= 300 && w <= 1200).toBe(true);
    });

    it("retries after the random wait, not a fixed one (#516)", async () => {
      vi.useFakeTimers();
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      const random = vi.spyOn(Math, "random").mockReturnValue(0.5);
      let calls = 0;
      const query: PageQuery<{ id: number }> = async () =>
        calls++ === 0 ? { data: null, error: timeout, count: null } : { data: table(1), error: null, count: 1 };
      const read = readAll("earnings_calls", query);
      await vi.advanceTimersByTimeAsync(749);
      expect(calls).toBe(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(await read).toHaveLength(1);
      expect(calls).toBe(2);
      random.mockRestore();
      warn.mockRestore();
      vi.useRealTimers();
    });

    it("does not retry any other error", async () => {
      let calls = 0;
      const query: PageQuery<{ id: number }> = async () => {
        calls++;
        return { data: null, error: { message: "permission denied", code: "42501" }, count: null };
      };
      await expect(readAll("company_prices", query)).rejects.toThrow("permission denied");
      expect(calls).toBe(1);
    });
  });
});
