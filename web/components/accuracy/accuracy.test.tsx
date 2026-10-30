// The /accuracy page (#135): each section from mocked rows, the period switch, the empty and error states, the
// no-internals scan, and the row-count check against the Data API stand-in. Rows are test data, not real counts.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AccuracyView, type AccuracyData } from "@/components/accuracy/AccuracyView";
import { TASK_KEY_PATTERN } from "@/lib/accuracy/summary";
import type { EvalSummaryRow, QualitySummaryRow } from "@/lib/queries/api";
import { loadEvalSummary, loadQualitySummary } from "@/lib/queries/accuracy";
import { IncompleteReadError } from "@/lib/queries/read";
import { fakeClient, type Row } from "@/tests/fake-postgrest";

const AS_OF = "2026-10-30T02:15:00+00:00";
const MEASURES = [
  "ai_outputs", "ai_published", "ai_rejected_citation", "ai_rejected_numbers", "ai_rejected_format", "ai_rejected_stopped",
  "ai_rejected_other", "ai_held_review", "loads", "loads_rolled_back", "loads_failed", "freshness_on_time_share", "drills_run",
  "drills_passed", "eval_gates_passed", "eval_gates_pending", "fields_hidden", "fields_labelled",
] as const;

function rowsFor(module: string, period: QualitySummaryRow["period"], base: number, overrides: Record<string, number> = {}): QualitySummaryRow[] {
  return MEASURES.map((measure, i) => ({
    module,
    period,
    measure,
    value: measure === "freshness_on_time_share" ? 0.95 : (overrides[measure] ?? base + i),
    n: measure === "freshness_on_time_share" ? 800 : null,
    label: measure === "freshness_on_time_share" ? "derived" : null,
    as_of: AS_OF,
  }));
}

const EVALS: EvalSummaryRow[] = [
  { eval_set: "policy_stance", model: "m", metric: "accuracy", value: 0.93, n_items: 60, run_at: "2026-10-20T10:00:00+00:00" },
];

const data = (over: Partial<AccuracyData> = {}): AccuracyData => ({
  period: "30d",
  quality: { ok: true, rows: [...rowsFor("policy", "30d", 100), ...rowsFor("policy", "90d", 200), ...rowsFor("policy", "all", 300)] },
  evals: { ok: true, rows: EVALS },
  freshness: [{ task_key: "site.quality_summary", last_checked_at: AS_OF, last_success_at: AS_OF, status: "ok" }],
  now: "2026-10-30T09:00:00+00:00",
  ...over,
});
const html = (d: AccuracyData) => renderToStaticMarkup(<AccuracyView data={d} />);
const text = (out: string) => out.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");

describe("AccuracyView", () => {
  it("asks its question and shows one section per module, with every panel", () => {
    const out = text(html(data()));
    expect(out).toContain("How often does this site hold something back, and do its checks work?");
    expect(out).toContain("Fed vs RBI policy tracker");
    for (const panel of ["AI outputs", "Loads", "Freshness", "Drills", "Evaluation gates", "Fields held back", "Passing evaluation runs"]) {
      expect(out).toContain(panel);
    }
  });

  it("shows the selected period's counts, formatted, and the freshness share as derived", () => {
    const out = text(html(data()));
    expect(out).toContain("Published");
    expect(out).toContain("101"); // 30d: base 100 + index 1 (ai_published)
    expect(out).not.toContain("201");
    expect(out).toContain("95.0%");
    expect(out).toContain("out of 800 task-days");
    expect(out).toContain("derived");
    expect(text(html(data({ period: "90d" })))).toContain("201");
    expect(text(html(data({ period: "all" })))).toContain("301");
  });

  it("switches the period through links, marking the current one", () => {
    const out = html(data({ period: "90d" }));
    expect(out).toMatch(/href="\/accuracy\?period=30d"/);
    expect(out).toMatch(/href="\/accuracy\?period=all"/);
    const tabs = out.match(/<a [^>]*href="\/accuracy\?period=[^"]*"[^>]*>/g) ?? [];
    expect(tabs).toHaveLength(3);
    expect(tabs.filter((t) => t.includes('aria-current="page"'))).toEqual([expect.stringContaining("period=90d")]);
  });

  it("gives each panel a source line naming the site's own records and a freshness line", () => {
    const out = html(data());
    expect(out.match(/Source: Finance Terminal&#x27;s own run and check records/g)?.length).toBeGreaterThanOrEqual(7);
    expect(out).toContain("Data to 30 Oct 2026");
  });

  it("says so when no drills ran, instead of showing zeros", () => {
    const rows = rowsFor("policy", "30d", 10, { drills_run: 0, drills_passed: 0 });
    expect(text(html(data({ quality: { ok: true, rows } })))).toContain("No drills run in the last 30 days.");
    const all = rowsFor("policy", "all", 10, { drills_run: 0, drills_passed: 0 });
    expect(text(html(data({ period: "all", quality: { ok: true, rows: all } })))).toContain("No drills have been run yet.");
  });

  it("shows a measure the projection lacks as not available, never 0", () => {
    const rows = rowsFor("policy", "30d", 10).filter((r) => r.measure !== "loads_failed");
    const out = html(data({ quality: { ok: true, rows } }));
    expect(text(out)).toContain("—");
  });

  it("adds a section for each further module, module 1 first", () => {
    const rows = [...rowsFor("equity", "30d", 5), ...rowsFor("policy", "30d", 100)];
    const out = text(html(data({ quality: { ok: true, rows } })));
    expect(out.indexOf("Fed vs RBI policy tracker")).toBeLessThan(out.indexOf("Equity screener and valuation"));
  });

  it("has an empty state with no rows for the period, and an error state when a read failed", () => {
    expect(text(html(data({ period: "all", quality: { ok: true, rows: rowsFor("policy", "30d", 1) } })))).toContain(
      "No counts have been recorded for the all time yet.",
    );
    const failed = html(data({ quality: { ok: false }, evals: { ok: false } }));
    expect(text(failed)).toContain("Couldn't load the counts.");
    expect(text(failed)).toContain("Couldn't load the evaluation runs.");
    expect(failed).toContain('data-state-kind="error"');
  });

  it("lists the passing evaluation runs in a table, and an empty state without any", () => {
    const out = html(data());
    expect(out).toContain('aria-label="Passing evaluation runs"');
    expect(text(out)).toContain("Policy stance");
    expect(text(out)).toContain("20 Oct 2026");
    expect(html(data({ evals: { ok: true, rows: [] } }))).toContain("No evaluation run has passed yet.");
  });

  it("shows no task key, run id or error text anywhere", () => {
    for (const d of [data(), data({ period: "all" }), data({ quality: { ok: false }, evals: { ok: false } })]) {
      const out = text(html(d));
      expect(out).not.toMatch(TASK_KEY_PATTERN);
      expect(out).not.toMatch(/run[ _]?id|task_key|IncompleteReadError|Traceback/i);
    }
    expect(text("see policy.fed_documents")).toMatch(TASK_KEY_PATTERN); // the scan itself works
  });

  it("uses no raw colours: green and red are for market moves only", () => {
    expect(html(data())).not.toMatch(/text-(up|down)|bg-(up|down)|#[0-9a-f]{3,6}\b/i);
  });
});

describe("the reads' row-count check", () => {
  const many = (n: number): Row[] =>
    Array.from({ length: n }, (_, i) => ({ module: `m${String(i).padStart(4, "0")}`, period: "30d", measure: "loads", value: i, n: null, label: null, as_of: AS_OF }));

  it("reads every quality row past the 1,000-row cut", async () => {
    const { db } = fakeClient({ quality_summary: many(2300) });
    expect(await loadQualitySummary(db)).toHaveLength(2300);
  });

  it("reads every evaluation row", async () => {
    const { db } = fakeClient({ eval_summary: EVALS as unknown as Row[] });
    expect(await loadEvalSummary(db)).toHaveLength(1);
  });

  it("fails loudly rather than return a cut list", async () => {
    const { db } = fakeClient({ quality_summary: many(10) });
    const bad = { from: () => ({ select: () => ({ order: () => ({ order: () => ({ order: () => ({ range: async () => ({ data: [], error: null, count: 5 }) }) }) }) }) }) };
    await expect(loadQualitySummary(bad as unknown as typeof db)).rejects.toBeInstanceOf(IncompleteReadError);
  });
});
