// lib/cmdbar.ts (K-07, #585): the gate, plain search's query and its count check, where each result goes, the answer stream and
// its shape check, and how a data answer's cells are shown.
import { describe, expect, it, vi } from "vitest";
import {
  CMDBAR_FIELD,
  ERROR_TEXT,
  SEARCH_PAGE,
  SUGGESTED_PAGES,
  askLabel,
  askQuestion,
  commandBarShown,
  fetchExamples,
  formatCell,
  groupHits,
  hitOf,
  isAiSummary,
  isShortcut,
  officialLink,
  pageHits,
  pageHref,
  parseAnswer,
  rangeTotal,
  readAnswer,
  rowMarket,
  searchIndex,
  searchQuery,
  searchTerm,
  shortcutLabel,
  type SearchRow,
} from "@/lib/cmdbar";
import { MINUS, NOT_AVAILABLE } from "@/lib/format";

describe("the gate (Spec 07 §3, N1)", () => {
  it("is hidden with no row, a hidden row, or no policies at all (fails closed)", () => {
    expect(commandBarShown([])).toBe(false);
    expect(commandBarShown(null)).toBe(false);
    expect(commandBarShown([{ field_key: CMDBAR_FIELD, status: "hidden", label: null }])).toBe(false);
    expect(commandBarShown([{ field_key: "site.guided_tour", status: "shown", label: null }])).toBe(false);
  });
  it("is shown for a shown or labelled row of site.command_bar only", () => {
    expect(CMDBAR_FIELD).toBe("site.command_bar");
    expect(commandBarShown([{ field_key: CMDBAR_FIELD, status: "shown", label: null }])).toBe(true);
    expect(commandBarShown([{ field_key: CMDBAR_FIELD, status: "labelled", label: "beta" }])).toBe(true);
  });
});

describe("the words", () => {
  it("asks with Design 01 §4.6's row and shows the platform's shortcut", () => {
    expect(askLabel("  repo rate cuts ")).toBe("Ask: 'repo rate cuts'");
    expect(shortcutLabel("MacIntel")).toBe("⌘K");
    expect(shortcutLabel("macOS")).toBe("⌘K");
    expect(shortcutLabel("Win32")).toBe("Ctrl K");
    expect(shortcutLabel(undefined)).toBe("Ctrl K");
  });
  it("takes ⌘K or Ctrl K, and nothing with another modifier", () => {
    const k = { key: "k", metaKey: false, ctrlKey: false, altKey: false, shiftKey: false };
    expect(isShortcut({ ...k, metaKey: true })).toBe(true);
    expect(isShortcut({ ...k, ctrlKey: true, key: "K" })).toBe(true);
    expect(isShortcut(k)).toBe(false);
    expect(isShortcut({ ...k, ctrlKey: true, shiftKey: true })).toBe(false);
    expect(isShortcut({ ...k, ctrlKey: true, key: "j" })).toBe(false);
  });
});

describe("plain search's term and query", () => {
  it("keeps letters, digits, spaces and &.'- only, so a visitor's text can't change the filter", () => {
    expect(searchTerm("  Reliance   Industries ")).toBe("Reliance Industries");
    expect(searchTerm('tcs),or(slug.neq.x')).toBe("tcs or slug.neq.x");
    expect(searchTerm('50% *"_\\')).toBe("50");
    expect(searchTerm("M&M")).toBe("M&M");
    expect(searchTerm("a")).toBeNull();
    expect(searchTerm("  %% ")).toBeNull();
    expect(searchTerm("x".repeat(200))).toHaveLength(80);
  });
  it("asks for the four subtypes with a page, entities by name or slug, and events up to today only", () => {
    const q = new URLSearchParams(searchQuery("rbi", "2026-11-17"));
    expect(q.get("select")).toBe("item_type,subtype,slug,title,parent_slug,on_date");
    expect(q.get("subtype")).toBe("in.(central_bank,company,policy_meeting,earnings_call)");
    expect(q.get("or")).toBe(
      '(and(item_type.eq.entity,or(title.ilike."*rbi*",slug.ilike."*rbi*")),' +
        'and(item_type.eq.event,on_date.lte.2026-11-17,or(title.ilike."*rbi*",slug.ilike."*rbi*",parent_slug.ilike."*rbi*")))',
    );
    expect(q.get("order")).toBe("item_type.asc,on_date.desc.nullsfirst,title.asc,slug.asc");
    expect(() => searchQuery("rbi", "17 Nov")).toThrow(RangeError);
  });
  it("reads a Content-Range total", () => {
    expect(rangeTotal("0-19/123")).toBe(123);
    expect(rangeTotal("*/0")).toBe(0);
    expect(rangeTotal(null)).toBeNull();
    expect(rangeTotal("0-19/*")).toBeNull();
  });
});

const row = (r: Partial<SearchRow>): SearchRow => ({
  item_type: "entity", subtype: "company", slug: "tcs", title: "Tata Consultancy Services Ltd.", parent_slug: null, on_date: null, ...r,
});

function fakeFetch(rows: unknown, contentRange: string | null, status = 200) {
  return vi.fn(async (...args: [string, RequestInit?]) => {
    void args;
    const headers = new Headers();
    if (contentRange) headers.set("content-range", contentRange);
    return new Response(JSON.stringify(rows), { status, headers });
  });
}

describe("searchIndex: one page, checked against its exact count", () => {
  const opts = { url: "https://x.supabase.co", key: "pk", today: "2026-11-17" };

  it("sends the browser key, the api profile, an exact count and the page's range", async () => {
    const f = fakeFetch([row({})], "0-0/1");
    const page = await searchIndex("tcs", 0, { ...opts, fetch: f as unknown as typeof fetch });
    expect(page).toEqual({ rows: [row({})], total: 1 });
    const [url, init] = f.mock.calls[0];
    expect(url).toMatch(/^https:\/\/x\.supabase\.co\/rest\/v1\/search_index\?/);
    const h = new Headers(init!.headers);
    expect(h.get("apikey")).toBe("pk");
    expect(h.get("accept-profile")).toBe("api");
    expect(h.get("prefer")).toBe("count=exact");
    expect(h.get("range")).toBe(`0-${SEARCH_PAGE - 1}`);
  });
  it("asks for the next page from where the last ended", async () => {
    const f = fakeFetch(Array.from({ length: 5 }, (_, i) => row({ slug: `c${i}` })), "20-24/25");
    const page = await searchIndex("co", 20, { ...opts, fetch: f as unknown as typeof fetch });
    expect(page.rows).toHaveLength(5);
    expect(new Headers(f.mock.calls[0][1]!.headers).get("range")).toBe(`20-${20 + SEARCH_PAGE - 1}`);
  });
  it("refuses a short page, a missing count, an error status and an off-shape body", async () => {
    const run = (f: ReturnType<typeof fakeFetch>) => searchIndex("tcs", 0, { ...opts, fetch: f as unknown as typeof fetch });
    await expect(run(fakeFetch([row({})], "0-0/30"))).rejects.toThrow(/1 rows where 20 were due/);
    await expect(run(fakeFetch([row({})], null))).rejects.toThrow(/no exact count/);
    await expect(run(fakeFetch([], "*/0", 500))).rejects.toThrow(/500/);
    await expect(run(fakeFetch({ rows: [] }, "*/0"))).rejects.toThrow(/off its shape/);
  });
});

describe("where a result goes", () => {
  it("opens the tracker, a company, a held meeting (its decision date) and a call", () => {
    expect(pageHref(row({ subtype: "central_bank", slug: "rbi" }))).toBe("/policy");
    expect(pageHref(row({}))).toBe("/companies/tcs");
    expect(pageHref(row({ item_type: "event", subtype: "policy_meeting", slug: "fomc-2026-09-17", parent_slug: "fed" }))).toBe(
      "/policy/fed/2026-09-17",
    );
    expect(pageHref(row({ item_type: "event", subtype: "policy_meeting", slug: "mpc-2026-10-07", parent_slug: "rbi" }))).toBe(
      "/policy/rbi/2026-10-07",
    );
    expect(pageHref(row({ item_type: "event", subtype: "earnings_call", slug: "tcs-call-q2-fy27", parent_slug: "tcs" }))).toBe(
      "/companies/tcs/calls/tcs-call-q2-fy27",
    );
  });
  it("has none for an index, another event type, or a slug that isn't one", () => {
    expect(pageHref(row({ subtype: "index", slug: "nifty-50" }))).toBeNull();
    expect(pageHref(row({ item_type: "event", subtype: "corporate_action", slug: "tcs-div-2026", parent_slug: "tcs" }))).toBeNull();
    expect(pageHref(row({ item_type: "event", subtype: "policy_meeting", slug: "fomc-next", parent_slug: "fed" }))).toBeNull();
    expect(pageHref(row({ slug: "../admin" }))).toBeNull();
  });
  it("lists results in Design 01 §4.6's groups, a meeting with its date", () => {
    const meeting = hitOf(row({ item_type: "event", subtype: "policy_meeting", slug: "fomc-2026-09-17", title: "FOMC meeting", parent_slug: "fed", on_date: "2026-09-16" }));
    expect(meeting).toMatchObject({ group: "Meetings", title: "FOMC meeting", detail: "17 Sep 2026" });
    const hits = [meeting!, hitOf(row({}))!, ...pageHits("screen"), hitOf(row({}))!];
    expect(groupHits(hits).map((g) => [g.group, g.hits.length])).toEqual([["Pages", 1], ["Companies", 1], ["Meetings", 1]]);
  });
  it("matches the site's visitor pages by label, never the admin's", () => {
    expect(pageHits("fed vs").map((h) => h.href)).toEqual(["/policy"]);
    expect(pageHits("admin")).toEqual([]);
    expect(pageHits("health")).toEqual([]);
    expect(pageHits(null)).toEqual([]);
  });
});

const DATA = {
  route: "data",
  state: "answered",
  summary: "The RBI cut the repo rate by 25 bps on 7 Oct 2026.",
  columns: [{ name: "decision_date", kind: "date" }, { name: "change_bps", kind: "number" }],
  rows: [["2026-10-07", "-25"]],
  sql: "SELECT decision_date, change_bps FROM semantic.policy_meetings LIMIT 200",
  citations: [],
  documents: [],
  view: "semantic.policy_meetings",
  pages: [],
  ms: 2400,
};

describe("parseAnswer: the route's final object, checked", () => {
  it("takes a data answer as sent", () => {
    const a = parseAnswer(DATA);
    expect(a.state).toBe("answered");
    expect(a.rows).toEqual([["2026-10-07", "-25"]]);
    expect(a.columns[1]).toEqual({ name: "change_bps", kind: "number" });
    expect(a.ms).toBe(2400);
  });
  it("is the error state for anything off its shape, never the raw value", () => {
    for (const bad of [null, "boom", { state: "exploded" }, { ...DATA, rows: [["2026-10-07"]] }, { ...DATA, rows: [[{}, "1"]] },
      { ...DATA, columns: [{ name: "x", kind: "money" }] }]) {
      const a = parseAnswer(bad);
      expect(a.state).toBe("error");
      expect(a.summary).toBe(ERROR_TEXT);
    }
  });
  it("turns the route's page answer (a search_index row) into its page, and keeps only site paths", () => {
    const page = parseAnswer({ ...DATA, state: "page", route: "page", pages: [{ item_type: "event", subtype: "policy_meeting", slug: "mpc-2026-10-07", title: "MPC meeting", parent_slug: "rbi" }] });
    expect(page.pages).toEqual([{ href: "/policy/rbi/2026-10-07", title: "MPC meeting" }]);
    const odd = parseAnswer({ ...DATA, state: "cant_answer", pages: [{ href: "https://evil.example", title: "x" }, { href: "//x", title: "y" }, ...SUGGESTED_PAGES] });
    expect(odd.pages).toEqual(SUGGESTED_PAGES);
  });
  it("checks a document answer's references and never keeps a link to RBI's pages", () => {
    const doc = (url: string | null, bank = "Reserve Bank of India") => ({ bank, label: "Resolution", title: "Monetary Policy Statement", date: "2026-10-07", event_slug: "mpc-2026-10-07", press_release: "2026-2027/1234", source_url: url });
    const a = parseAnswer({ ...DATA, route: "document", columns: [], rows: [], citations: [{ text: "It held the rate.", refs: [{ document: 0, char_start: 10, char_end: 40, quote: "held" }] }], documents: [doc("https://www.rbi.org.in/x")] });
    expect(a.state).toBe("answered");
    expect(a.documents[0].source_url).toBeNull();
    expect(a.documents[0].press_release).toBe("2026-2027/1234");
    const bad = parseAnswer({ ...DATA, route: "document", citations: [{ text: "x", refs: [{ document: 3, char_start: 0, char_end: 1, quote: "x" }] }], documents: [doc(null)] });
    expect(bad.state).toBe("error");
    expect(officialLink("https://www.federalreserve.gov/newsevents/x.htm")).toBe("https://www.federalreserve.gov/newsevents/x.htm");
    expect(officialLink("https://rbi.org.in/x")).toBeNull();
    expect(officialLink("https://website.rbi.org.in/x")).toBeNull();
    expect(officialLink("http://www.federalreserve.gov/")).toBeNull();
    expect(officialLink("not a url")).toBeNull();
  });
});

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
}

describe("readAnswer: the stream", () => {
  it("passes each status line on, then returns the final object, across chunk boundaries", async () => {
    const seen: string[] = [];
    const final = JSON.stringify(DATA);
    const a = await readAnswer(
      streamOf('{"status": "Understanding the', ' question…"}\n{"status": "Writing SQL…"}\n', final.slice(0, 20), `${final.slice(20)}\n`),
      (l) => seen.push(l),
    );
    expect(seen).toEqual(["Understanding the question…", "Writing SQL…"]);
    expect(a.state).toBe("answered");
  });
  it("reads a final object with no newline at the end", async () => {
    expect((await readAnswer(streamOf(JSON.stringify({ ...DATA, state: "paused" })), () => {})).state).toBe("paused");
  });
  it("is the error state for a stream that ends early or isn't JSON", async () => {
    expect((await readAnswer(streamOf('{"status": "Writing SQL…"}\n'), () => {})).state).toBe("error");
    expect((await readAnswer(streamOf("Internal Server Error\n"), () => {})).state).toBe("error");
  });
});

describe("askQuestion and fetchExamples never show a failure's own words", () => {
  it("posts the question and reads the stream", async () => {
    const f = vi.fn(async () => new Response(`${JSON.stringify({ ...DATA, state: "paused", summary: "AI questions are paused for now; search still works." })}\n`));
    const a = await askQuestion("repo rate", { onStatus: () => {}, fetch: f as unknown as typeof fetch });
    expect(a.state).toBe("paused");
    expect(f).toHaveBeenCalledWith("/api/cmdbar/ask", expect.objectContaining({ method: "POST", body: JSON.stringify({ question: "repo rate" }) }));
  });
  it("is the error state for a refused or failed request", async () => {
    const refused = vi.fn(async () => new Response('{"state": "invalid"}', { status: 400 }));
    expect((await askQuestion("x", { onStatus: () => {}, fetch: refused as unknown as typeof fetch })).state).toBe("error");
    const down = vi.fn(async () => {
      throw new TypeError("Failed to fetch: ECONNREFUSED 10.0.0.1");
    });
    const a = await askQuestion("x", { onStatus: () => {}, fetch: down as unknown as typeof fetch });
    expect(a).toMatchObject({ state: "error", summary: ERROR_TEXT });
  });
  it("lists usable examples only, at most six, and none on a failure", async () => {
    const ex = (i: number, answer: unknown = DATA) => ({ question: `Q${i}`, module: "policy", display_order: i, answer });
    const ok = vi.fn(async () => new Response(JSON.stringify({ examples: [ex(1), ex(2, null), ex(3, { state: "boom" }), ...[4, 5, 6, 7, 8, 9].map((i) => ex(i))] })));
    const list = await fetchExamples({ fetch: ok as unknown as typeof fetch });
    expect(list.map((e) => e.question)).toEqual(["Q1", "Q4", "Q5", "Q6", "Q7", "Q8"]);
    const fail = vi.fn(async () => new Response("x", { status: 502 }));
    expect(await fetchExamples({ fetch: fail as unknown as typeof fetch })).toEqual([]);
  });
});

describe("a data answer's cells", () => {
  const now = "2026-11-17T02:30:00Z";
  it("marks the AI line, never the route's plain row count", () => {
    expect(isAiSummary({ summary: DATA.summary, rows: DATA.rows })).toBe(true);
    expect(isAiSummary({ summary: "1 row", rows: [["x"]] })).toBe(false);
    expect(isAiSummary({ summary: "12 rows", rows: Array.from({ length: 12 }, () => ["x"]) })).toBe(false);
    expect(isAiSummary({ summary: null, rows: [] })).toBe(false);
  });
  it("finds a row's market from its own currency or market column", () => {
    const cols = [{ name: "company", kind: "text" as const }, { name: "currency", kind: "text" as const }];
    expect(rowMarket(cols, ["TCS", "INR"])).toBe("IN");
    expect(rowMarket(cols, ["Apple", "USD"])).toBe("US");
    expect(rowMarket([{ name: "market", kind: "text" }], ["US"])).toBe("US");
    expect(rowMarket(cols, ["TCS", null])).toBeUndefined();
  });
  it("shows each number exactly as the row holds it, grouped by its market from five whole digits", () => {
    expect(formatCell("-25", "number", undefined, now)).toBe(`${MINUS}25`);
    expect(formatCell("2026", "number", "IN", now)).toBe("2026");
    expect(formatCell("1234567.891", "number", "IN", now)).toBe("12,34,567.891");
    expect(formatCell("1234567.891", "number", "US", now)).toBe("1,234,567.891");
    expect(formatCell("0.1234567", "number", "US", now)).toBe("0.1234567");
  });
  it("shows dates, times, yes or no, text, and a dash for a null", () => {
    expect(formatCell("2026-10-07", "date", undefined, now)).toBe("07 Oct 2026");
    expect(formatCell("2026-10-07T04:30:00+00:00", "timestamp", undefined, now)).toBe("7 Oct, 10:00 IST");
    expect(formatCell(true, "boolean", undefined, now)).toBe("Yes");
    expect(formatCell("Hold", "text", undefined, now)).toBe("Hold");
    expect(formatCell(null, "number", undefined, now)).toBe(NOT_AVAILABLE);
  });
});
