// The /earnings list rendered to HTML as the server sends it (2B-20, #183; Spec 02B §13.7; 02B.1 C6): each of the six statuses in
// words, the role-held suffix, the window of a "No transcript found" row as the view returned it, the date or filed-on text,
// the link to each company's tab, the default sort, the source lines, the exact-count read, and the banned-words scan.
// Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EarningsList, EARNINGS_DESCRIPTION } from "@/components/company/calls/EarningsList";
import { listDateKey, listSourceLines, listStatusText } from "@/lib/equity/calls";
import type { EarningsListRow } from "@/lib/queries/api";
import { readEarningsList, type EarningsListData } from "@/lib/queries/calls";
import { IncompleteReadError } from "@/lib/queries/read";
import { bannedCallWordsIn, bannedWordsIn } from "@/tests/banned-words";
import { fakeClient, type Row } from "@/tests/fake-postgrest";

const text = (markup: string) =>
  markup.replace(/<span class="sr-only">[^<]*<\/span>/g, "").replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

function row(over: Partial<EarningsListRow> = {}): EarningsListRow {
  return {
    company_slug: "tcs", company_name: "Tata Consultancy Services Ltd.", short_name: "TCS", call_slug: "q2-fy27", fiscal_label: "Q2 FY27",
    call_date: "2026-10-09", filed_at: "2026-10-12T09:00:00+05:30", exchange: "NSE", route: "exchange_pdf", status: "stored",
    summary_state: "available", roles_partial: false, window_from: null, window_to: null, ...over,
  };
}
const none = (slug: string, name: string): EarningsListRow =>
  row({ company_slug: slug, company_name: name, short_name: null, call_slug: null, fiscal_label: null, call_date: null, filed_at: null, exchange: null, route: null, status: "none_found", summary_state: null, roles_partial: null, window_from: "2026-07-01", window_to: "2026-11-05" });

const rows: EarningsListRow[] = [
  row(),
  row({ company_slug: "infy", company_name: "Infosys Ltd.", short_name: "Infosys", call_date: "2026-10-16", summary_state: "not_generated" }),
  row({ company_slug: "wipro", company_name: "Wipro Ltd.", short_name: "Wipro", call_date: null, filed_at: "2026-10-20T09:00:00+05:30", summary_state: "held_back" }),
  row({ company_slug: "hcl", company_name: "HCL Technologies Ltd.", short_name: "HCLTech", call_date: "2026-10-14", status: "on_company_website", route: "curated_link", summary_state: "not_generated" }),
  row({ company_slug: "tech-m", company_name: "Tech Mahindra Ltd.", short_name: "TechM", call_date: "2026-10-22", status: "unreadable", summary_state: "not_generated" }),
  none("itc", "ITC Ltd."),
  row({ company_slug: "ltim", company_name: "LTIMindtree Ltd.", short_name: "LTIM", call_date: "2026-10-18", roles_partial: true }),
];
const ok = (r: EarningsListRow[]): EarningsListData => ({ ok: true, readAt: "2026-11-05T10:00:00+00:00", rows: r });
const html = (d: EarningsListData) => renderToStaticMarkup(<EarningsList data={d} freshness={null} />);

describe("the list", () => {
  const out = html(ok(rows));
  const t = text(out);

  it("shows each status in words", () => {
    for (const w of [
      "Summary and figures",
      "Summary not generated yet",
      "AI summary held back until it passes review",
      "Transcript on the company's website",
      "Transcript is a scanned image; not analysed",
      "No transcript found",
    ])
      expect(t).toContain(w);
  });

  it("adds the roles suffix to the role-held row only", () => {
    expect(t).toContain("Summary and figures · roles partly read");
    expect(t.match(/roles partly read/g)).toHaveLength(1);
    expect(listStatusText(row({ roles_partial: false }))).toBe("Summary and figures");
  });

  it("gives the window the view returned on a no-transcript row, never a fixed date", () => {
    expect(t).toContain("No transcript found among filings since 1 Jul 2026");
    const moved = text(html(ok([{ ...none("itc", "ITC Ltd."), window_from: "2026-01-02" }])));
    expect(moved).toContain("since 2 Jan 2026");
    expect(moved).not.toContain("1 Jul 2026");
    expect(text(html(ok([{ ...none("itc", "ITC Ltd."), window_from: null }])))).not.toContain("among filings");
  });

  it("shows the call date, or the day its transcript was filed when the date isn't known", () => {
    expect(t).toContain("9 Oct 2026");
    expect(t).toContain("Transcript filed on 20 Oct 2026");
  });

  it("links each company to its Earnings calls tab", () => {
    for (const r of rows) expect(out).toContain('href="/companies/' + r.company_slug + '/calls"');
  });

  it("opens sorted by call date, newest first, a company with no call last", () => {
    const names = ["TechM", "Wipro", "LTIM", "Infosys", "HCLTech", "TCS", "ITC Ltd."];
    // the filed-on date (20 Oct) places Wipro between TechM (22 Oct) and LTIM (18 Oct)
    const at = names.map((n) => out.indexOf(">" + n + "<"));
    expect(at.every((i) => i > 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(listDateKey(rows[2])).toBe("2026-10-20");
    expect(out).toContain('aria-sort="descending"');
  });

  it("has the header, description, source lines and freshness", () => {
    expect(t).toContain("Earnings calls");
    expect(t).toContain(EARNINGS_DESCRIPTION);
    expect(t).toContain("Source: NSE");
    expect(t).toContain("the company's website, linked from its exchange filing");
    expect(listSourceLines([row()])).toEqual(["Source: NSE"]);
    expect(listSourceLines([row({ exchange: "BSE" })])).toEqual(["Source: NSE", "Source: BSE"]);
  });

  it("uses none of the words the earnings pages never use, and no colour or raw value", () => {
    expect(bannedCallWordsIn(t)).toEqual([]);
    expect(bannedWordsIn(EARNINGS_DESCRIPTION)).toEqual([]);
    expect(out).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(|text-(red|green)|bg-(red|green)/i);
  });
});

describe("the states", () => {
  it("never leaves the panel blank", () => {
    expect(text(html(ok([])))).toContain("No companies are listed yet.");
    expect(text(html({ ok: false, readAt: "2026-11-05T10:00:00+00:00" }))).toContain("Couldn't load this panel. Try again.");
  });
});

describe("the read", () => {
  const store = (n: number): Row[] => Array.from({ length: n }, (_, i) => ({ company_slug: "co-" + String(i).padStart(4, "0"), company_name: "Co " + i, short_name: null, call_slug: null, fiscal_label: null, call_date: null, filed_at: null, exchange: null, route: null, status: "none_found", summary_state: null, roles_partial: null, window_from: null, window_to: null }));
  it("reads every row whole against the exact count, past one request's cap", async () => {
    const { db, seen } = fakeClient({ earnings_latest: store(1200) });
    const got = await readEarningsList(db);
    expect(got).toHaveLength(1200);
    expect(seen.every((s) => s.countExact)).toBe(true);
    expect(seen.length).toBeGreaterThan(1);
  });
  it("throws, never returns a cut list, when the view can't be read", async () => {
    const { db } = fakeClient({});
    await expect(readEarningsList(db)).rejects.toBeInstanceOf(IncompleteReadError);
  });
});
