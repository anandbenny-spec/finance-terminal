// /admin/cmdbar (Spec 07 §4 Examples, §9 step 4; K-09 part 2, #588): the list says for every chip whether the box shows it, shows
// each stored answer as the box would, offers "Mark checked" only for an answer, and the mark carries the time of the answer the
// page showed. Never blank.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }), usePathname: () => "/admin/cmdbar" }));

import { ExampleList } from "@/components/admin/CmdbarExamplesView";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { AdminDeps } from "@/lib/admin/api";
import { markExample, parseAdminExamples, readAdminExamples, type AdminExample } from "@/lib/admin/cmdbar";

const html = (node: ReactNode) => renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
const text = (markup: string) =>
  markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/\s+/g, " ").trim();

const T0 = "2026-11-17T04:30:00.123456+00:00";
const NOW = "2026-11-17T12:00:00Z";
const DATA = {
  state: "answered",
  route: "data",
  summary: "The RBI cut the repo rate by 25 basis points on 5 December 2025.",
  columns: [
    { name: "meeting_date", kind: "date" },
    { name: "policy_rate_change_bps", kind: "number" },
  ],
  rows: [["2025-12-05", "-25"]],
  sql: "SELECT meeting_date, policy_rate_change_bps FROM policy_meetings LIMIT 200",
  view: "policy_meetings",
  ms: 1200,
};
const BODY = {
  examples: [
    { id: 1, question: "When did the RBI last change the repo rate, and by how much?", module: "policy", display_order: 1, answer: DATA, data_version: "v1", verified: true, updated_at: T0 },
    { id: 2, question: "Which Nifty 50 banks trade below one times book value?", module: "equity", display_order: 2, answer: { ...DATA, summary: null, rows: [] }, data_version: "v2", verified: false, updated_at: T0 },
    { id: 3, question: "What changed in the RBI's latest resolution?", module: "policy", display_order: 3, answer: { state: "cant_answer", route: "document" }, data_version: null, verified: false, updated_at: T0 },
    { id: 4, question: "Not stored yet", module: "equity", display_order: 4, answer: null, data_version: null, verified: false, updated_at: T0 },
    { id: "x", question: "malformed" },
  ],
};

describe("parseAdminExamples", () => {
  it("keeps every well-formed row, checked or not, and marks which are answers", () => {
    const got = parseAdminExamples(BODY)!;
    expect(got.map((e) => [e.id, e.verified, e.answerable, e.answer?.state ?? null])).toEqual([
      [1, true, true, "answered"],
      [2, false, true, "answered"],
      [3, false, false, "cant_answer"],
      [4, false, false, null],
    ]);
    expect(got[0].updatedAt).toBe(T0);
  });

  it("is null for anything that isn't the route's shape", () => {
    expect(parseAdminExamples(null)).toBeNull();
    expect(parseAdminExamples({ examples: "no" })).toBeNull();
    expect(parseAdminExamples([])).toBeNull();
  });
});

describe("ExampleList", () => {
  const examples = parseAdminExamples(BODY)!;
  const out = html(<ExampleList examples={examples} now={NOW} busy={null} notes={{ 3: "Only an answer can be marked checked." }} onMark={() => {}} />);
  const t = text(out);

  it("counts the checked chips and says which ones the box shows", () => {
    expect(t).toContain("1 of 4 checked. Only checked answers appear as chips in the box.");
    expect(t.match(/Checked: the box shows it/g)).toHaveLength(1);
    expect(t.match(/Not checked: the box doesn't show it/g)).toHaveLength(3);
  });

  it("shows each stored answer as the box would, with its summary marked", () => {
    expect(t).toContain("The RBI cut the repo rate by 25 basis points on 5 December 2025.");
    expect(t).toContain("AI summary");
    expect(t).toContain("That isn't in this site's data.");
    expect(t).toContain("No answer is stored yet: the daily task stores one on its next run.");
  });

  it("offers the mark only for an answer, and the way off for a checked one", () => {
    expect(t.match(/Take the mark off/g)).toHaveLength(1);
    const buttons = [...out.matchAll(/<button[^>]*>(?:(?!<\/button>).)*Mark checked<\/button>/g)].map((m) => m[0]);
    expect(buttons).toHaveLength(3);
    expect(buttons.filter((b) => /\sdisabled=""/.test(b))).toHaveLength(2);
    expect(t).toContain("Only an answer can be marked checked.");
  });

  it("names its source on every chip and is never blank", () => {
    expect(t.match(/Source: the command bar's example answers \(ops\)/g)).toHaveLength(4);
    expect(text(html(<ExampleList examples={[]} now={NOW} busy={null} notes={{}} onMark={() => {}} />))).toBe(
      "No example answers are stored yet. The daily task stores them once it is enabled.",
    );
  });
});

function fakeDeps(status: number, body: unknown, seen: { url: string; init: RequestInit }[]): AdminDeps {
  return {
    token: async () => "a.b.c",
    fetch: (async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    }) as unknown as typeof fetch,
  };
}

describe("the route calls", () => {
  const example = parseAdminExamples(BODY)![1] as AdminExample;

  it("reads the list with the admin's token", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const r = await readAdminExamples(fakeDeps(200, BODY, seen));
    expect(r.examples).toHaveLength(4);
    expect(seen[0].url).toBe("/api/admin/cmdbar/examples");
    expect((seen[0].init.headers as Record<string, string>).Authorization).toBe("Bearer a.b.c");
  });

  it("a mark carries the time of the answer the page showed; taking it off carries none", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    expect((await markExample(example, true, fakeDeps(200, { id: 2, verified: true }, seen))).outcome).toBe("done");
    expect(seen[0].url).toBe("/api/admin/cmdbar/examples/2/check");
    expect(seen[0].init.method).toBe("POST");
    expect(JSON.parse(String(seen[0].init.body))).toEqual({ verified: true, updated_at: T0 });
    await markExample(example, false, fakeDeps(200, { id: 2, verified: false }, seen));
    expect(JSON.parse(String(seen[1].init.body))).toEqual({ verified: false });
  });

  it("names the route's two refusals, and anything else is a failure", async () => {
    expect((await markExample(example, true, fakeDeps(409, { state: "changed" }, []))).outcome).toBe("changed");
    expect((await markExample(example, true, fakeDeps(409, { state: "not_answered" }, []))).outcome).toBe("not_answered");
    expect((await markExample(example, true, fakeDeps(503, { detail: "x" }, []))).outcome).toBe("failed");
  });
});
