// The results-day moves panel on the Earnings calls tab (#471; Spec 02B §5.12; migration 0044): hidden means no panel and no
// read; shown means the figures of api.results_moves with their signs and triangles, the release line, the left-out counts, the
// "not a forecast" line, the source and the error state. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LEFT_OUT_TEXT, RESULTS_NOT_A_FORECAST, leftOutLine, moveLines, releaseNote, rowsOfCall } from "@/lib/equity/resultsMoves";
import type { FieldPolicyRow, ResultsMoveRow } from "@/lib/queries/api";
import { readCallsTab, type CallsTabData } from "@/lib/queries/calls";
import { bannedCallWordsIn } from "@/tests/banned-words";
import { HELD, SHOWN, call, calls } from "@/tests/call-rows";
import { page } from "@/tests/company-rows";
import { fakeClient, type Row } from "@/tests/fake-postgrest";

const text = (markup: string) =>
  markup.replace(/<span class="sr-only">[^<]*<\/span>/g, "").replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const html = (c: CallsTabData) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={page({ tab: "calls", overview: null, calls: c })} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );

const open = (status: "shown" | "labelled" = "labelled"): FieldPolicyRow[] => [...SHOWN, { field_key: "equity.results_move", status, label: "derived" }];
const hidden = (): FieldPolicyRow[] => [...SHOWN, { field_key: "equity.results_move", status: "hidden", label: "derived" }];

function row(over: Partial<ResultsMoveRow> = {}): ResultsMoveRow {
  return {
    company_slug: "tcs", company_name: "Tata Consultancy Services Ltd.", currency: "INR", results_slug: "tcs-results-q2-fy27", call_slug: "q2-fy27",
    fiscal_label: "Q2 FY27", occurs_at: "2026-10-09T12:30:00+05:30", window_key: "d1", pre_date: "2026-10-08", post_date: "2026-10-09",
    own_change_pct: 3.5, nifty_change_pct: 1, excess_pct: 2.5, left_out_reason: null, attribution: "Source: NSE", ...over,
  };
}
const d5 = (over: Partial<ResultsMoveRow> = {}) =>
  row({ window_key: "d5", pre_date: "2026-10-08", post_date: "2026-10-15", own_change_pct: -2, nifty_change_pct: 0.5, excess_pct: -2.5, ...over });
const withRows = (rows: ResultsMoveRow[], policies = open()) => calls({ policies, results: { state: "ok", rows } });

describe("hidden", () => {
  it("shows no panel and no word of it while equity.results_move is hidden or has no row", () => {
    for (const policies of [hidden(), SHOWN]) {
      const t = text(html(withRows([row(), d5()], policies)));
      expect(t).not.toContain("Results and the first move");
      expect(t).not.toContain(RESULTS_NOT_A_FORECAST);
    }
    expect(text(html(calls({ policies: HELD })))).not.toContain("Results and the first move");
  });
});

describe("shown", () => {
  it("shows both windows' figures from the rows, each with its sign and triangle", () => {
    const out = html(withRows([row(), d5()]));
    const t = text(out);
    expect(t).toContain("Results and the first move");
    expect(t).toContain("First close after the release");
    expect(t).toContain("Fifth close after the release");
    expect(t).toContain("+3.50%");
    expect(t).toContain("+2.50%");
    expect(t).toContain("−2.00%");
    expect(t).toContain("−2.50%");
    expect(t).toContain("8 Oct 2026 to 9 Oct 2026");
    expect(out).toContain("<svg");
  });

  it("states the release in IST and which close counts: in market hours, the same day's", () => {
    const t = text(html(withRows([row(), d5()])));
    expect(t).toContain("Released 9 Oct 2026, 12:30 IST.");
    expect(t).toContain("during market hours");
    expect(t).toContain("the move includes the part of the day before the release");
  });

  it("states an evening release's first close as the next session's date", () => {
    const evening = [row({ occurs_at: "2026-10-09T18:30:00+05:30", post_date: "2026-10-12" }), d5({ occurs_at: "2026-10-09T18:30:00+05:30" })];
    const note = releaseNote(evening) ?? "";
    expect(note).toContain("Released 9 Oct 2026, 18:30 IST.");
    expect(note).toContain("The first close after it is 12 Oct 2026.");
    expect(note).not.toContain("market hours");
  });

  it("puts the not-a-forecast line, the NSE source and no banned word under the panel", () => {
    const t = text(html(withRows([row(), d5()])));
    expect(t).toContain(`${RESULTS_NOT_A_FORECAST}.`);
    expect(t).toContain("Source: NSE");
    expect(bannedCallWordsIn(t)).toEqual([]);
  });

  it("counts a left-out window and says why in words, with no figure for it", () => {
    const left = d5({ own_change_pct: null, nifty_change_pct: null, excess_pct: null, pre_date: null, post_date: null, left_out_reason: "dividend" });
    const t = text(html(withRows([row(), left])));
    expect(t).toContain(LEFT_OUT_TEXT.dividend);
    expect(t).toContain("1 of 2 windows are left out");
    expect(t).not.toContain("−2.00%");
    expect(leftOutLine(moveLines([row(), left]))).toBe("1 of 2 windows are left out; the reason is given in the row.");
    expect(leftOutLine(moveLines([row(), d5()]))).toBeNull();
  });

  it("says every left-out reason without a task, table or file name", () => {
    for (const words of Object.values(LEFT_OUT_TEXT)) expect(words).not.toMatch(/\b(equity|core|api|ops)\.[a-z_]+|_/);
  });

  it("names the 'no excess' windows as left out only when the view says so: a Nifty 50 move over other dates leaves the excess out", () => {
    const t = text(html(withRows([row(), d5({ excess_pct: null, nifty_change_pct: null, left_out_reason: "dates_differ" })])));
    expect(t).toContain(LEFT_OUT_TEXT.dates_differ);
  });

  it("shows only the selected call's filing", () => {
    const other = row({ call_slug: "q1-fy27", results_slug: "tcs-results-q1-fy27", own_change_pct: 9.99, excess_pct: 9.99 });
    expect(rowsOfCall([row(), d5(), other], "q2-fy27")).toHaveLength(2);
    expect(text(html(withRows([row(), d5(), other])))).not.toContain("9.99");
  });

  it("says no filing is linked when the call has none", () => {
    expect(text(html(withRows([])))).toContain("No results filing from 2025 is linked to this call");
  });

  it("holds back only this panel when its read failed", () => {
    const t = text(html(calls({ policies: open(), results: { state: "error" } })));
    expect(t).toContain("Results and the first move");
    expect(t).toContain("Couldn't load this panel. Try again.");
    expect(t).toContain("Guidance record");
  });

  it("carries the field's label when it is labelled, and none when it is shown", () => {
    expect(text(html(withRows([row(), d5()], open("labelled"))))).toMatch(/Results and the first move[^]*derived/);
    expect(text(html(withRows([row(), d5()], open("shown"))))).toContain("Results and the first move");
  });
});

describe("the read", () => {
  const callRow = call() as unknown as Row;
  const store = (policies: FieldPolicyRow[], extra: Record<string, Row[]> = {}) => ({
    earnings_calls: [callRow],
    field_policy: policies as unknown as Row[],
    call_guidance: [],
    guidance_hit_rates: [],
    ai_outputs: [],
    ai_citations: [],
    citation_pages: [],
    call_figures: [],
    ...extra,
  });

  it("does not query api.results_moves at all while the field is hidden", async () => {
    const { db, seen } = fakeClient(store(hidden()));
    const got = await readCallsTab(db, "tcs", undefined);
    expect(got.results).toEqual({ state: "hidden" });
    expect(seen.some((s) => s.view === "results_moves")).toBe(false);
  });

  it("reads the company's rows once shown: filtered to the company, against the exact count, past one request's cap", async () => {
    const many: Row[] = Array.from({ length: 1200 }, (_, i) => ({ ...row({ results_slug: "r" + String(i).padStart(5, "0") }) }));
    const { db, seen } = fakeClient(store(open(), { results_moves: many }));
    const got = await readCallsTab(db, "tcs", undefined);
    expect(got.results.state === "ok" && got.results.rows).toHaveLength(1200);
    const reqs = seen.filter((s) => s.view === "results_moves");
    expect(reqs.length).toBeGreaterThan(1);
    expect(reqs.every((s) => s.countExact && s.params.get("company_slug") === "eq.tcs")).toBe(true);
  });

  it("keeps the rest of the tab when its own read fails (the view is absent from the store, so the request answers 404)", async () => {
    const { db } = fakeClient(store(open()));
    const got = await readCallsTab(db, "tcs", undefined);
    expect(got.results).toEqual({ state: "error" });
    expect(got.calls).toHaveLength(1);
  });
});
