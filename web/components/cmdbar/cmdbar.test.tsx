// The command bar in the top bar (K-07, #585; Spec 07 §3, §7, §10 item 5; Design 01 §4.6). The flag-off test: with
// `site.command_bar` hidden (no field-policy row), the top bar renders exactly as it did on `main` before K-07. Then the box
// when shown, and every answer state in Design 01 §4.6's words, with no raw error ever on screen.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const shown = vi.hoisted(() => ({ value: false }));
vi.mock("@/lib/queries/cmdbar", () => ({ readCommandBarShown: async () => shown.value }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }), usePathname: () => "/" }));

import { AnswerView } from "@/components/cmdbar/AnswerView";
import { GatedSlot } from "@/components/cmdbar/CommandBarSlot";
import { CommandBarSlotView, HIDDEN_SLOT_CLASS } from "@/components/cmdbar/SlotView";
import { TopBar } from "@/components/shell/TopBar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CANT_TEXT, ERROR_TEXT, NOT_FOUND_TEXT, PAUSED_TEXT, PLACEHOLDER, parseAnswer, type Answer } from "@/lib/cmdbar";
import { MINUS } from "@/lib/format";

const html = (node: ReactNode) => renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
const text = (markup: string) =>
  markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/\s+/g, " ").trim();

/** The slot exactly as TopBar rendered it on `main` before K-07 (2d3d990). */
const MAIN_SLOT = '<div data-slot="command-bar" class="mx-auto hidden max-w-160 flex-1 md:block"></div>';

beforeEach(() => {
  shown.value = false;
});

describe("the flag-off test (Spec 07 §10 item 5): hidden, the top bar is unchanged", () => {
  it("renders the empty slot exactly as main had it, and nothing of the box", () => {
    const out = html(<TopBar />);
    expect(out.match(/data-slot="command-bar"/g)).toHaveLength(1);
    expect(out).toContain(`<a class="flex min-h-6 items-center gap-2 font-mono text-ui font-medium whitespace-nowrap text-ink-1 no-underline lg:min-w-50" href="/"><span aria-hidden="true" class="size-2 shrink-0 bg-highlight"></span>Finance Terminal</a>${MAIN_SLOT}<div class="ml-auto flex items-center justify-end md:ml-0 lg:min-w-50"></div></header>`);
    expect(text(out)).toBe("Menu Finance Terminal");
    expect(out).not.toMatch(/<input|cmdk|Ctrl K|⌘K|Search and ask/);
  });
  it("keeps the hidden slot's classes as main had them", () => {
    expect(HIDDEN_SLOT_CLASS).toBe("mx-auto hidden max-w-160 flex-1 md:block");
    expect(html(<CommandBarSlotView shown={false} />)).toBe(MAIN_SLOT);
  });
  it("after the read: no row (hidden) is the empty slot; a shown key is the box", async () => {
    shown.value = false;
    expect(html(await GatedSlot())).toBe(MAIN_SLOT);
    shown.value = true;
    const out = html(await GatedSlot());
    expect(out).toContain(`placeholder="${PLACEHOLDER}"`);
  });
});

describe("the box, shown (Design 01 §4.6)", () => {
  const out = html(<CommandBarSlotView shown />);

  it("is a combobox input with the placeholder and the shortcut tag, from md", () => {
    expect(out).toMatch(/<input[^>]*role="combobox"/);
    expect(out).toContain(`placeholder="${PLACEHOLDER}"`);
    expect(out).toContain('maxLength="500"');
    expect(out).toMatch(/<kbd[^>]*font-mono[^>]*>Ctrl K<\/kbd>/);
    expect(out).toContain('<div class="hidden w-full md:block">');
  });
  it("has a labelled search button below md that opens the full-screen sheet", () => {
    expect(out).toMatch(/<button[^>]*aria-label="Search and ask"[^>]*aria-haspopup="dialog"|<button[^>]*aria-haspopup="dialog"[^>]*aria-label="Search and ask"/);
    expect(out).toMatch(/aria-label="Search and ask"[^>]*md:hidden|md:hidden[^>]*aria-label="Search and ask"/);
  });
  it("holds a polite status line for the working stages", () => {
    expect(out).toContain('role="status" aria-live="polite"');
  });
  it("takes back the gap below md, so a visitor's top bar fits 320 px", () => {
    expect(out).toMatch(/data-slot="command-bar" class="[^"]*max-md:-mr-3/);
  });
});

const BASE = { route: null, summary: null, columns: [], rows: [], sql: null, citations: [], documents: [], view: null, pages: [], ms: 0 };
const view = (a: Partial<Answer> & { state: Answer["state"] }) =>
  html(<AnswerView answer={parseAnswer({ ...BASE, ...a, state: a.state === "error" ? "boom" : a.state })} now="2026-11-17T02:30:00Z" onNavigate={() => {}} />);

describe("the answer states, in Design 01 §4.6's words", () => {
  it("Limit reached, and the paused state: the same line", () => {
    expect(text(view({ state: "paused", summary: PAUSED_TEXT }))).toBe(PAUSED_TEXT);
    expect(text(view({ state: "limit", summary: PAUSED_TEXT }))).toBe(PAUSED_TEXT);
  });
  it("Can't answer, with the suggested pages", () => {
    const out = view({ state: "cant_answer", summary: CANT_TEXT, pages: [{ href: "/policy", title: "Fed vs RBI" }, { href: "/screener", title: "Screener" }] as never });
    expect(text(out)).toBe(`${CANT_TEXT} Fed vs RBI Screener`);
    expect(out).toContain('href="/policy"');
  });
  it("Not found in the documents", () => {
    expect(text(view({ state: "not_found", summary: NOT_FOUND_TEXT }))).toBe(NOT_FOUND_TEXT);
  });
  it("a page: the link the index names, no AI text", () => {
    const out = view({ state: "page", route: "page", pages: [{ item_type: "event", subtype: "policy_meeting", slug: "fomc-2026-09-17", title: "FOMC meeting", parent_slug: "fed" }] as never });
    expect(out).toContain('href="/policy/fed/2026-09-17"');
    expect(out).not.toContain("AI summary");
  });
  it("never shows a raw error: an off-shape answer is the error state's words only", () => {
    const out = view({ state: "error", summary: "Traceback: psycopg.errors.QueryCanceled at 10.0.0.1" });
    expect(text(out)).toBe(ERROR_TEXT);
    expect(out).not.toMatch(/Traceback|psycopg|10\.0\.0\.1/);
  });
});

describe("a data answer (Design 01 §4.6)", () => {
  const DATA = {
    state: "answered" as const,
    route: "data",
    summary: "The RBI cut the repo rate by 25 bps on 7 Oct 2026.",
    columns: [{ name: "decision_date", kind: "date" }, { name: "change_bps", kind: "number" }] as never,
    rows: [["2026-10-07", "-25"]] as never,
    sql: "SELECT decision_date, change_bps\nFROM semantic.policy_meetings\nLIMIT 200",
    view: "semantic.policy_meetings",
    ms: 2400,
  };

  it("marks the summary 'AI summary', shows the rows formatted, Show SQL with a copy button, and the view and time", () => {
    const out = view(DATA);
    const t = text(out);
    expect(t).toContain("AI summary The RBI cut the repo rate by 25 bps on 7 Oct 2026.");
    expect(t).toContain("decision_date");
    expect(t).toContain("07 Oct 2026");
    expect(t).toContain(`${MINUS}25`);
    expect(out).toMatch(/<details[^>]*><summary[^>]*>Show SQL<\/summary>/);
    expect(out).toContain("SELECT decision_date, change_bps\nFROM semantic.policy_meetings\nLIMIT 200");
    expect(t).toContain("Copy SQL");
    expect(t).toMatch(/semantic\.policy_meetings · 2\.4 s$/);
  });
  it("shows the plain row count unmarked when no AI line passed its check", () => {
    const out = view({ ...DATA, summary: "1 row" });
    expect(text(out)).not.toContain("AI summary");
    expect(text(out)).toMatch(/^1 row /);
  });
});

describe("a document answer, until K-08's chips", () => {
  it("marks it AI, numbers each sentence's documents, and lists them by name, date and press-release number, with no link", () => {
    const out = view({
      state: "answered",
      route: "document",
      citations: [{ text: "The MPC kept the repo rate unchanged.", refs: [{ document: 0, char_start: 10, char_end: 60, quote: "kept unchanged" }] }],
      documents: [{ bank: "Reserve Bank of India", label: "Resolution", title: "Resolution of the Monetary Policy Committee", date: "2026-10-07", event_slug: "mpc-2026-10-07", press_release: "2026-2027/1234", source_url: "https://www.rbi.org.in/x" }],
      ms: 3100,
    } as never);
    const t = text(out);
    expect(t).toContain("AI summary The MPC kept the repo rate unchanged. [1]");
    expect(t).toContain("[1] Reserve Bank of India · Resolution of the Monetary Policy Committee · 7 Oct 2026 · Press release 2026-2027/1234");
    expect(out).not.toMatch(/rbi\.org\.in|<a /);
  });
});
