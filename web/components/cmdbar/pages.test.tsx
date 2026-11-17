// The command bar's public explanations (K-12, #589; Spec 07 §7, §10 item 5). About's "What is logged" names every
// `ops.cmdbar_log` column and nothing else (read from the migrations, so a new column fails this test until the copy names it);
// /methodology's section reads its gate results from api.eval_summary and names the judge by model. Both render only while
// `site.command_bar` is published: hidden, each page is byte for byte what it was, and no eval read is made.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EvalSummaryRow } from "@/lib/queries/api";

const state = vi.hoisted(() => ({
  shown: false,
  evals: { ok: true, rows: [] } as { ok: true; rows: EvalSummaryRow[] } | { ok: false },
  gates: null as import("@/components/cmdbar/MethodologyLines").Gates | null,
  evalReads: 0,
}));
vi.mock("@/lib/queries/cmdbar", () => ({ readCommandBarShown: async () => state.shown }));
vi.mock("@/lib/queries/accuracy", () => ({
  readEvalSummary: async () => {
    state.evalReads += 1;
    return state.evals;
  },
}));
// The pages' own wrappers suspend on the read, which a static render can't wait for; here they render the view at the state the
// test sets, so the whole page can be compared hidden and shown. The gated functions below are the real ones.
vi.mock("@/components/cmdbar/AboutLogged", async (importOriginal) => {
  const m = await importOriginal<typeof import("@/components/cmdbar/AboutLogged")>();
  return { ...m, CommandBarLogged: () => <m.LoggedView shown={state.shown} /> };
});
vi.mock("@/components/cmdbar/MethodologyLines", async (importOriginal) => {
  const m = await importOriginal<typeof import("@/components/cmdbar/MethodologyLines")>();
  return { ...m, CommandBarMethodology: () => <m.MethodologyView shown={state.shown} gates={state.gates} /> };
});

import AboutPage from "@/app/about/page";
import MethodologyPage from "@/app/methodology/page";
import { GatedLogged, LoggedView, NOT_LIVE } from "@/components/cmdbar/AboutLogged";
import { GatedMethodology, MethodologyView, SECTION_ID, type Gates } from "@/components/cmdbar/MethodologyLines";
import { CMDBAR_GATES, LOG_ROW_ID, LOGGED, gateLines } from "@/lib/cmdbar";
import { NOT_AVAILABLE } from "@/lib/format";
import { bannedWordsIn } from "@/tests/banned-words";

const html = (node: ReactNode) => renderToStaticMarkup(<>{node}</>);
const text = (markup: string) =>
  markup
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&ldquo;|&rdquo;/g, '"')
    .replace(/\s+/g, " ")
    .trim();

const repo = (path: string) => fileURLToPath(new URL(`../../../${path}`, import.meta.url));

/** `ops.cmdbar_log`'s columns: its `create table` (0010) plus any later `alter table … add/drop column`, in migration order. */
function logColumns(): string[] {
  const dir = repo("supabase/migrations");
  const columns: string[] = [];
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    const sql = readFileSync(`${dir}/${file}`, "utf8");
    const create = /create table ops\.cmdbar_log \(([\s\S]*?)\n\);/.exec(sql);
    if (create) {
      for (const line of create[1].split("\n")) {
        const name = /^\s*([a-z_][a-z0-9_]*)\s+(?!key\b)/.exec(line)?.[1];
        if (name && !["primary", "constraint", "unique", "check", "foreign"].includes(name)) columns.push(name);
      }
    }
    for (const alter of sql.matchAll(/alter table (?:only )?ops\.cmdbar_log\b([^;]*);/g)) {
      for (const add of alter[1].matchAll(/add column (?:if not exists )?([a-z_][a-z0-9_]*)/g)) columns.push(add[1]);
      for (const drop of alter[1].matchAll(/drop column (?:if exists )?([a-z_][a-z0-9_]*)/g)) columns.splice(columns.indexOf(drop[1]), 1);
    }
  }
  return columns;
}

const ROWS: EvalSummaryRow[] = [
  { eval_set: "cmdbar_sql", model: "claude-haiku-5-5", metric: "result_match", value: 0.88, n_items: 60, run_at: "2026-11-17T10:00:00Z" },
  { eval_set: "cmdbar_sql", model: "claude-haiku-5-5", metric: "result_match_dev", value: 0.5, n_items: 12, run_at: "2026-11-18T10:00:00Z" },
  { eval_set: "cmdbar_router", model: "claude-haiku-5-5", metric: "route_accuracy", value: 0.95, n_items: 40, run_at: "2026-11-16T20:00:00Z" },
  { eval_set: "cmdbar_router", model: "claude-haiku-4-5", metric: "route_accuracy", value: 0.91, n_items: 40, run_at: "2026-11-10T10:00:00Z" },
  { eval_set: "retrieval", model: "voyage-4-lite", metric: "recall_at_8", value: 0.9, n_items: 20, run_at: "2026-11-15T09:00:00Z" },
  { eval_set: "policy_stance", model: "claude-haiku-5-5", metric: "accuracy", value: 0.97, n_items: 50, run_at: "2026-11-01T10:00:00Z" },
];

beforeEach(() => {
  state.shown = false;
  state.evals = { ok: true, rows: ROWS };
  state.gates = null;
  state.evalReads = 0;
});

describe("About's \"What is logged\" names every ops.cmdbar_log column and nothing else (Spec 07 §10 item 5)", () => {
  const columns = logColumns();

  it("reads the log's columns from the migrations", () => {
    expect(columns).toEqual(expect.arrayContaining(["visitor_hash", "question", "generated_sql", "cost_usd", "error_class", "at"]));
    expect(columns).not.toContain("ip");
  });

  it("each column is named by exactly one line, and every line names a column the log has", () => {
    const named = LOGGED.flatMap((l) => l.columns);
    expect(new Set(named).size).toBe(named.length);
    expect([...named, ...LOG_ROW_ID].sort()).toEqual([...columns].sort());
  });

  it("matches what the route writes (ft.cmdbar.log's COLUMNS, plus the database's id and at)", () => {
    const py = readFileSync(repo("py/src/ft/cmdbar/log.py"), "utf8");
    const written = [...(/^COLUMNS = \(([\s\S]*?)\)/m.exec(py)?.[1] ?? "").matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    expect(written.length).toBeGreaterThan(0);
    expect([...written, "id", "at"].sort()).toEqual([...columns].sort());
  });

  it("shown: the list is exactly the logged lines, in order, with the retention and what is never kept", () => {
    const out = html(<LoggedView shown />);
    const list = /<ul[^>]*data-cmdbar-logged=""[^>]*>([\s\S]*?)<\/ul>/.exec(out)?.[1] ?? "";
    const items = [...list.matchAll(/<li>([\s\S]*?)<\/li>/g)].map((m) => text(m[1]));
    expect(items).toEqual(LOGGED.map((l) => l.text));
    const t = text(out);
    expect(t).toContain("a daily-rotating anonymous hash");
    expect(t).toContain("Each entry is kept for 90 days and then deleted");
    expect(t).toContain("The log holds no IP address, no browser details and no cookie, and asking a question needs no account");
    expect(t).toContain("Anthropic's Claude models");
    expect(t).toContain("Voyage AI");
    expect(t).not.toContain(NOT_LIVE);
    expect(t.replace(/90 days/, "")).not.toMatch(/\d/);
    expect(bannedWordsIn(t)).toEqual([]);
    expect(out).not.toMatch(/href=/);
  });

  it("hidden: exactly the paragraph About had before, and the gate read decides", async () => {
    expect(html(<LoggedView shown={false} />)).toBe(`<p>${NOT_LIVE}</p>`);
    state.shown = false;
    expect(html(await GatedLogged())).toBe(`<p>${NOT_LIVE}</p>`);
    state.shown = true;
    expect(html(await GatedLogged())).toBe(html(<LoggedView shown />));
  });

  it("the About page: hidden it is unchanged; shown only its command bar paragraph changes, inside \"What is logged\"", () => {
    state.shown = false;
    const hidden = html(<AboutPage />);
    expect(text(hidden)).toContain("command bar is not live on this link");
    state.shown = true;
    const shown = html(<AboutPage />);
    const block = html(<LoggedView shown />);
    expect(shown).toBe(hidden.replace(`<p>${NOT_LIVE}</p>`, block));
    const at = shown.indexOf(block);
    expect(at).toBeGreaterThan(shown.indexOf('id="logged"'));
    expect(at).toBeLessThan(shown.indexOf('id="sources"'));
  });
});

describe("/methodology's command bar section (Spec 07 §7)", () => {
  const passed: Gates = { ok: true, lines: gateLines(ROWS) };

  it("the three gates are gates.yaml's, with the same eval set and metric", () => {
    const yaml = readFileSync(repo("py/config/eval/gates.yaml"), "utf8");
    for (const g of CMDBAR_GATES) {
      const entry = new RegExp(`- key: ${g.key}\\n([\\s\\S]*?)(?=\\n  - key:|$)`).exec(yaml)?.[1] ?? "";
      expect(entry, g.key).toMatch(new RegExp(`eval_set: ${g.evalSet}\\b`));
      expect(entry, g.key).toMatch(new RegExp(`metric: ${g.metric}\\b`));
    }
    expect(CMDBAR_GATES.map((g) => g.key)).toEqual(["cmdbar_sql", "cmdbar_router", "retrieval"]);
  });

  it("each gate's result is its newest passing row from api.eval_summary, formatted as /accuracy formats it", () => {
    expect(gateLines(ROWS).map((l) => l.result)).toEqual([
      { value: "0.88", items: "60", date: "17 Nov 2026" },
      { value: "0.95", items: "40", date: "17 Nov 2026" },
      { value: "0.90", items: "20", date: "15 Nov 2026" },
    ]);
    expect(gateLines([]).map((l) => l.result)).toEqual([null, null, null]);
    expect(gateLines([{ ...ROWS[0], n_items: null }])[0].result?.items).toBe(NOT_AVAILABLE);
  });

  it("shown: answers only from the data or cited documents, the gates from the view, the judge by model", () => {
    const out = html(<MethodologyView shown gates={passed} />);
    const t = text(out);
    expect(t).toContain("It answers only from the data or cited documents");
    expect(t).toContain("never from general knowledge");
    expect(t).toContain("Money in different currencies is never compared, ranked or summed");
    expect(t).toContain("Earnings-call transcripts are not searched");
    expect(t).toContain("a sentence without a verified citation is dropped");
    expect(t).toContain("checked by an AI model, Claude Opus, in a separate session that built none of it");
    expect(t).not.toMatch(/Anand|reviewed by|labelled by/i);
    const gates = /<ul[^>]*data-cmdbar-gates=""[^>]*>([\s\S]*?)<\/ul>/.exec(out)?.[1] ?? "";
    const items = [...gates.matchAll(/<li>([\s\S]*?)<\/li>/g)].map((m) => text(m[1]));
    expect(items).toEqual([
      "Data questions, the share whose rows equal those of a checked answer: 0.88 , on 60 questions, run on 17 Nov 2026 .",
      "Routing, the share of questions sent to the right kind of answer: 0.95 , on 40 questions, run on 17 Nov 2026 .",
      "Document search, the share of questions whose expected passage was among those found: 0.90 , on 20 questions, run on 15 Nov 2026 .",
    ]);
    // every figure in the section is a row's: outside the gates' list there is no digit at all
    expect(text(out.replace(gates, ""))).not.toMatch(/\d/);
    expect(bannedWordsIn(t)).toEqual([]);
    expect(out).not.toMatch(/href="https?:/);
    for (const [, path] of out.matchAll(/href="\/([^"#]*)(#[^"]*)?"/g)) {
      expect(existsSync(fileURLToPath(new URL(`../../app/${path}/page.tsx`, import.meta.url))), path).toBe(true);
    }
  });

  it("shown: a gate with no passing run says so, and a failed read is the error state (never the reason)", () => {
    const none = text(html(<MethodologyView shown gates={{ ok: true, lines: gateLines([]) }} />));
    expect(none.match(/no passing run is recorded yet/g)).toHaveLength(3);
    const failed = html(<MethodologyView shown gates={{ ok: false }} />);
    expect(failed).toContain('data-state-kind="error"');
    expect(text(failed)).toContain("Couldn't load the evaluation results.");
    expect(failed).not.toContain("data-cmdbar-gates");
  });

  it("its panel title differs from its heading, and its id is new on the page", () => {
    const out = html(<MethodologyView shown gates={passed} />);
    expect(out).toContain(`<h2 id="${SECTION_ID}"`);
    const heading = text(/<h2 id="cmdbar"[^>]*>([\s\S]*?)<\/h2>/.exec(out)?.[1] ?? "");
    const panel = text(/<h3 id="cmdbar-title"[^>]*>([\s\S]*?)<\/h3>/.exec(out)?.[1] ?? "");
    expect(heading).toBe("The command bar");
    expect(panel).toBe("Questions and answers");
  });

  it("hidden: nothing at all, and no eval read", async () => {
    expect(html(<MethodologyView shown={false} gates={passed} />)).toBe("");
    state.shown = false;
    expect(html(await GatedMethodology())).toBe("");
    expect(state.evalReads).toBe(0);
    state.shown = true;
    expect(html(await GatedMethodology())).toBe(html(<MethodologyView shown gates={passed} />));
    expect(state.evalReads).toBe(1);
    state.evals = { ok: false };
    expect(html(await GatedMethodology())).toBe(html(<MethodologyView shown gates={{ ok: false }} />));
  });

  it("the page: hidden it is unchanged; shown, only the section is added, between the US slice and the limitations", () => {
    state.shown = false;
    const hidden = html(<MethodologyPage />);
    expect(hidden).not.toContain('id="cmdbar"');
    state.shown = true;
    state.gates = passed;
    const shown = html(<MethodologyPage />);
    const section = html(<MethodologyView shown gates={passed} />);
    expect(shown.replace(section, "")).toBe(hidden);
    const at = shown.indexOf(section);
    expect(at).toBeGreaterThan(shown.indexOf('<h2 id="us"'));
    expect(at).toBeLessThan(shown.indexOf('<h2 id="limits"'));
  });
});
