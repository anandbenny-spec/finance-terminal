// The value chain card (VC-06, #191; Spec 02B §12.7, §12.10, §12.11 item 5): the Overview panel and the sub-page from the rows of the
// 0034/0040 views. Held back while the field policies are hidden (the held note and nothing else); once released, every figure as
// filed with its source line, filing date and boundary; each state in its words (absent, filed as 0, held, not shown, 15 months,
// restated); changes in words with no colour; the arrows equal; the flow read as sentences; "View as table"; a company with no
// related-party rows is "not available", never "none"; a six-month sum names its period; the median only from three peers; the
// banned words; and nothing that can push the page sideways at 320 px. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { ValueChainPage } from "@/components/company/value-chain/ValueChainPage";
import { ValueChainFlow } from "@/components/company/value-chain/ValueChainFlow";
import { AS_REPORTED, RESTATED_NOTE, VC_HELD, flowOf, yearsOf } from "@/lib/equity/valuechain";
import type { FieldPolicyRow, TaskFreshnessRow } from "@/lib/queries/api";
import type { ValueChainPageData } from "@/lib/queries/valuechain";
import { TooltipProvider } from "@/components/ui/tooltip";
import { bannedValueChainWordsIn } from "@/tests/banned-words";
import { company, page } from "@/tests/company-rows";
import { FY24, FY25, FY26, card, groupCompany, peerRow, product, rpt, segment, type Over } from "@/tests/value-chain-rows";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

const policy = (field: string, status: FieldPolicyRow["status"] = "labelled", label = "As reported by the company"): FieldPolicyRow => ({ field_key: field, status, label });
const ALL_SHOWN: FieldPolicyRow[] = ["equity.brsr_values", "equity.group_companies", "equity.rpt_counterparties", "equity.segments", "equity.peer_group"].map((f) => policy(f));
const VALUES_HIDDEN: FieldPolicyRow[] = [policy("equity.brsr_values", "hidden")];

const FRESH: TaskFreshnessRow[] = [
  { task_key: "equity.brsr", last_success_at: "2026-10-31T03:00:00Z", last_run_at: "2026-10-31T03:00:00Z", last_status: "succeeded", overdue_after: "10 days" } as unknown as TaskFreshnessRow,
];

const PRODUCTS = [product(FY26, 1, "Automotive", 70), product(FY26, 2, "Farm equipment", 22.4), product(FY26, 3, "Financial services", 5), product(FY26, 4, "Other", 2.6)];

function overview(rows = [...card(FY25, { fields: { within_india_share: { value: 88 } } }), ...card(FY26, { assured: "reasonable" })], policies: FieldPolicyRow[] | null = ALL_SHOWN, products = PRODUCTS, family = "operating") {
  return renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView
        data={page({ company: company({ slug: "mandm", name: "Mahindra & Mahindra Ltd.", short_name: "M&M", valuation_family: family }), overview: { prices: [], metrics: [], peers: [], valueChain: { rows, products, policies } } })}
        basis="consolidated"
        freshness={FRESH}
      />
    </TooltipProvider>,
  );
}

/** The panel's own markup: from its section's opening tag to the section's close. */
function panel(out: string, id: string): string {
  const start = out.lastIndexOf("<section", out.indexOf(`id="${id}"`));
  expect(start).toBeGreaterThanOrEqual(0);
  return out.slice(start, out.indexOf("</section>", start) + "</section>".length);
}

describe("the Value chain panel while equity.brsr_values is hidden", () => {
  it.each([
    ["no policy row (fails closed)", [] as FieldPolicyRow[]],
    ["the policy read failed", null],
    ["the policy is hidden", VALUES_HIDDEN],
  ])("shows the held note and nothing else: %s", (_, policies) => {
    const p = panel(overview(undefined, policies), "panel-value-chain");
    const t = text(p);
    expect(t).toContain("Value chain");
    expect(t).toContain(VC_HELD);
    for (const word of ["90.4%", "Automotive", "Within India", "Official source", "Years, group and peers", "Filed 4 Jul", "Data to", "Update delayed", "Suppliers", "Customers"]) expect(t).not.toContain(word);
    expect(p).toContain('data-state-kind="held"');
    expect(t).toContain("Source: the company's BRSR, filed on NSE");
    expect(p).not.toContain("example.test");
  });

  it("holds back a released policy whose view still returns null columns (fails closed)", () => {
    const rows = card(FY26).map((r) => ({ ...r, state: null, value: null }));
    expect(text(panel(overview(rows, ALL_SHOWN), "panel-value-chain"))).toContain(VC_HELD);
  });

  it("leaves the other Overview panels as they are", () => {
    const t = text(overview(undefined, VALUES_HIDDEN));
    expect(t).toContain("Peers");
    expect(t).toContain("Key figures");
  });
});

describe("the Value chain panel once released", () => {
  const out = overview();
  const p = panel(out, "panel-value-chain");
  const t = text(p);

  it("draws the latest year's figures as filed, with the company's top three products by share of turnover", () => {
    for (const s of ["90.4%", "9.7%", "0.07%", "79.33%", "4,480", "13.9%", "4.1%", "9.8%", "13.2%", "46 countries", "28 states", "Automotive", "70%", "Farm equipment", "22.4%", "Financial services", "5%"]) expect(t).toContain(s);
    expect(t).not.toContain("Other 2.6%"); // only the top three
    expect(t).toContain("FY 2025-26");
    expect(t).not.toContain("88%"); // the year before's value is not this year's
  });

  it("states the source, the filing date, the boundary and the caveat, and links the official list and the sub-page", () => {
    expect(t).toContain("Source: the company's BRSR, filed on NSE");
    expect(t).toContain("Filed 4 Jul 2026");
    expect(t).toContain("Standalone, as reported in the BRSR");
    expect(t).toContain(AS_REPORTED);
    expect(p).toContain('href="https://example.test/brsr/mandm"');
    expect(p).toContain('rel="noopener noreferrer"');
    expect(p).toContain('href="/companies/mandm/value-chain"');
    expect(t).toContain("Years, group and peers ›");
  });

  it("puts the assurance line on the filing's own flag, for the openness figures only", () => {
    expect(t).toContain("Reasonable assurance, as stated in the BRSR");
    const without = text(panel(overview(card(FY26)), "panel-value-chain"));
    expect(without).not.toContain("assurance");
  });

  it("draws three columns, the arrows all the same, no colour on a share and the company in the India colour", () => {
    expect((p.match(/data-flow-arrow/g) ?? []).length).toBe(2);
    const arrows = [...p.matchAll(/<div[^>]*data-flow-arrow[^>]*>[\s\S]*?<\/div>/g)].map((m) => m[0]);
    expect(arrows[0]).toBe(arrows[1]);
    expect(p).toContain("border-series-2");
    const body = p.slice(0, p.lastIndexOf('<div class="mt-auto')); // the footer's "Update delayed" is the one place the warning colour belongs
    for (const hue of ["text-up", "text-down", "text-gain", "text-loss", "green", "red", "text-warn", "bg-up", "bg-down"]) expect(body).not.toContain(hue);
    expect(p).not.toMatch(/[▲▼]/); // no Delta triangle
    expect(p).toContain("md:grid-cols-[1fr_auto_1fr_auto_1fr]");
    expect(p).toContain("grid-cols-1"); // the columns stack below md
  });

  it("reads the flow as sentences for screen readers and offers View as table", () => {
    const aria = /data-flow-aria="[^"]*">([^<]*)</.exec(p)?.[1] ?? "";
    expect(aria).toContain("Value chain, FY 2025-26, Standalone, as reported in the BRSR.");
    expect(aria).toContain("Inputs from within India: 90.4%");
    expect(aria).toContain("Customers.");
    expect(p).toContain('aria-label="View as table"');
    expect(p).toContain("aria-hidden"); // the diagram itself is hidden from readers: the sentences carry it
  });

  it("keeps clear of the sideways scroll at 320 px: every column can shrink and long words wrap", () => {
    expect(p).toContain("min-w-0");
    expect(p).toContain("break-words");
    expect(p).not.toMatch(/\bw-\[\d+px\]|min-w-\[\d+px\]/);
  });

  it("uses none of the banned words in its own text", () => {
    expect(bannedValueChainWordsIn(t.replace(/Automotive|Farm equipment|Financial services|Other/g, ""))).toEqual([]);
  });
});

describe("each state, in its words", () => {
  const html = (fields: Record<string, Over>, opts: { family?: string; exchange?: "NSE" | "BSE" } = {}) =>
    text(panel(overview(card(FY26, { fields, exchange: opts.exchange }), ALL_SHOWN, PRODUCTS, opts.family ?? "operating"), "panel-value-chain"));

  it("an absent value is 'Not reported', with the full sentence in the explainer", () => {
    const t = html({ msme_share: { state: "absent" } });
    expect(t).toContain("Not reported");
    expect(t).toContain("Inputs sourced directly from MSMEs and small producers: Not reported in the company's BRSR");
    expect(t).not.toContain("0%, as filed");
  });

  it("a zero on dealers or trading houses is 'Filed as 0', and for a bank or an insurer says it does not apply", () => {
    const fields = { dealer_share: { state: "zero" as const }, dealers: { state: "zero" as const }, top10_dealer_share: { state: "not_shown" as const } };
    const operating = html(fields);
    expect(operating).toContain("Filed as 0");
    expect(operating).not.toContain("not applicable to banks and insurers");
    for (const family of ["lender", "insurer"]) expect(html(fields, { family })).toContain("Filed as 0 (not applicable to banks and insurers)");
  });

  it("a zero on any other field is 0% as filed", () => {
    const t = html({ exports_share: { state: "zero" } });
    expect(t).toContain("Exports, share of turnover: 0%, as filed");
  });

  it("a top-10 share whose parent is 0 or absent is not shown at all", () => {
    const t = html({ dealer_share: { state: "zero" }, top10_dealer_share: { state: "not_shown" } });
    expect(t).not.toContain("top 10 dealers and distributors: ");
    expect(t).toContain("not shown, because the share it is a part of is 0 or not reported");
  });

  it("a held value shows 'Held back' with the stored reason, never the number", () => {
    const t = html({ msme_share: { state: "held", reason: "outside 0–100% as filed" } });
    expect(t).toContain("Held back");
    expect(t).toContain("held back (outside 0–100% as filed)");
    expect(t).not.toContain("59.62");
  });

  it("a restated value shows the year's own value and the next filing's comparative", () => {
    const t = html({ within_india_share: { restated: true, next: 89.9 } });
    expect(t).toContain("90.4%");
    expect(t).toContain("Restated in the FY 2026-27 BRSR to 89.9%");
  });

  it("the explainer says the figures are the XBRL's, and says what a shown restatement is, only where the year has one (#433)", () => {
    const plain = text(panel(overview(), "panel-value-chain"));
    expect(plain).toContain("tagged in the BRSR's XBRL file");
    expect(plain).toContain("Not applicable");
    expect(plain).toContain("where the XBRL file carries 0");
    expect(plain).not.toContain(RESTATED_NOTE);
    const restated = text(panel(overview([...card(FY26, { fields: { within_india_share: { restated: true, next: 89.9 } } })]), "panel-value-chain"));
    expect(restated).toContain(RESTATED_NOTE);
  });

  it("a year of other than 12 months says how long it was", () => {
    const t = text(panel(overview(card({ label: "FY 2023-24", start: "2023-01-01", end: "2024-03-31" }, { months: 15 }), ALL_SHOWN), "panel-value-chain"));
    expect(t).toContain("15 months, 1 Jan 2023 – 31 Mar 2024");
  });

  it("a BSE filing says BSE", () => {
    expect(html({}, { exchange: "BSE" })).toContain("Source: the company's BRSR, filed on BSE");
  });

  it("without products it says so on the company's box", () => {
    const t = text(panel(overview(card(FY26), ALL_SHOWN, []), "panel-value-chain"));
    expect(t).toContain("Products: Not reported in the company's BRSR");
  });

  it("with no filing at all it says none is published; a failed read says it couldn't load", () => {
    expect(text(panel(overview([], ALL_SHOWN), "panel-value-chain"))).toContain("No BRSR filing is published for this company yet.");
    const failed = renderToStaticMarkup(
      <TooltipProvider>
        <CompanyView data={page({ overview: { prices: [], metrics: [], peers: [], valueChain: { rows: null, products: null, policies: ALL_SHOWN } } })} basis="consolidated" freshness={null} />
      </TooltipProvider>,
    );
    expect(text(panel(failed, "panel-value-chain"))).toContain("Couldn't load this panel");
  });
});

describe("ValueChainFlow", () => {
  const years = yearsOf(card(FY26));
  const flow = flowOf(years[0], PRODUCTS, { company: "M&M", family: "operating" });
  it("renders three columns and equal arrows at any content", () => {
    const out = renderToStaticMarkup(<ValueChainFlow flow={flow} />);
    expect((out.match(/data-flow-arrow/g) ?? []).length).toBe(2);
    expect(out).toContain("Suppliers");
    expect(out).toContain("Customers");
    expect(out).toContain("Within the group");
  });
  it("builds the table view from the same figures", () => {
    expect(flow.table.find((r) => r.label.startsWith("Dealers and distributors"))?.value).toBe("4,480");
    expect(flow.table.map((r) => r.side)).toEqual(expect.arrayContaining(["Suppliers", "The company", "Customers", "Within the group"]));
  });
});

// ---------------------------------------------------------------------------------------------------------------------------------
// The sub-page

function pageData(over: Partial<ValueChainPageData> = {}): ValueChainPageData {
  return {
    rows: [
      ...card(FY24, { fields: { within_india_share: { change: undefined } } }),
      ...card(FY25, { boundary: "consolidated", fields: { within_india_share: { changeReason: "boundary_change" }, msme_share: { change: 1.5 }, dealers: { change: -120 } } }),
      ...card(FY26, { boundary: "consolidated", assured: "limited", fields: { within_india_share: { change: 2.4 }, msme_share: { restated: true, next: 9.9 } } }),
    ],
    products: PRODUCTS,
    policies: ALL_SHOWN,
    group: [groupCompany(1, "Mahindra Lifespace Developers Ltd.", 51.25), groupCompany(2, "Mahindra Holidays & Resorts Ltd.", 66.9)],
    rpt: [
      rpt("Sale of goods or services", 1, "Mahindra Defence Systems Ltd.", 4.2e10),
      rpt("Sale of goods or services", 2, "Mahindra Electric Automobile Ltd.", 1.5e10),
      rpt("Purchase of goods or services", 1, "Mahindra Last Mile Mobility Ltd.", 2.7e10),
    ],
    segments: [segment(1, "Automotive", 6e11, 70), segment(2, "Farm equipment", 2.57e11, 30)],
    peers: [
      ...["mandm", "maruti", "tatamotors", "eichermot", "bajaj"].flatMap((s, i) => [peerRow(s, "within_india_share", 80 + i), peerRow(s, "msme_share", 5 + i), peerRow(s, "dealer_share", 70 + i), peerRow(s, "rpt_purchases_share", i), peerRow(s, "rpt_sales_share", i)]),
    ],
    ...over,
  };
}

const sub = (data: ValueChainPageData, year?: string) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <ValueChainPage
        company={company({ slug: "mandm", name: "Mahindra & Mahindra Ltd.", short_name: "M&M", peer_group_label: "Autos", valuation_family: "operating" })}
        actions={[]}
        indexNames={{}}
        data={data}
        year={year}
        freshness={FRESH}
        now="2026-10-31T09:00:00.000Z"
      />
    </TooltipProvider>,
  );

describe("the sub-page while everything is hidden", () => {
  it("shows the header and one held note per panel, no figure", () => {
    const out = sub(pageData({ policies: [] }));
    const t = text(out);
    expect(t).toContain("Mahindra & Mahindra Ltd.: value chain");
    for (const note of ["Value chain held back until it passes review", "Related-party counterparties held back until they pass review", "Group companies held back until they pass review", "Segments held back until they pass review", "Peer comparison held back until the value chain passes review"]) expect(t).toContain(note);
    for (const word of ["90.4%", "Mahindra Defence", "Mahindra Lifespace", "Automotive", "4,480", "Official source"]) expect(t).not.toContain(word);
  });
});

describe("the sub-page once released", () => {
  const out = sub(pageData());
  const t = text(out);

  it("has the five panels and a way back", () => {
    for (const id of ["panel-vc-flow", "panel-vc-fields", "panel-vc-group", "panel-vc-segments", "panel-vc-peers"]) expect(out).toContain(`id="${id}"`);
    expect(out).toContain('href="/companies/mandm"');
  });

  it("shows the latest year by default and another year when asked, with year links", () => {
    const flow = text(panel(out, "panel-vc-flow"));
    expect(flow).toContain("FY 2025-26");
    expect(panel(out, "panel-vc-flow")).toContain('href="/companies/mandm/value-chain?year=2024-25"');
    expect(text(panel(sub(pageData(), "2023-24"), "panel-vc-flow"))).toContain("Standalone, as reported in the BRSR");
  });

  it("tells a change in words under the figure, and a boundary change as not compared, with no colour", () => {
    const flow = panel(out, "panel-vc-flow");
    const ft = text(flow);
    expect(ft).toContain("up 2.4 pp on FY 2024-25");
    expect(ft).toContain("Limited assurance, as stated in the BRSR");
    expect(ft).toContain("Restated in the FY 2026-27 BRSR to 9.9%");
    const y25 = text(panel(sub(pageData(), "2024-25"), "panel-vc-flow"));
    expect(y25).toContain("Standalone in FY 2023-24, consolidated in FY 2024-25: not compared");
    expect(y25).toContain("down 120 on FY 2023-24");
    expect(flow).not.toMatch(/[▲▼]/);
  });

  it("lists every field by year with its item reference, and the restated note as a footnote", () => {
    const fields = text(panel(out, "panel-vc-fields"));
    for (const s of ["FY 2023-24", "FY 2024-25", "FY 2025-26", "P8-EI-4, Within India", "P1-EI-9, Sales (b)", "Boundary", "Consolidated", "4,480"]) expect(fields).toContain(s);
    expect(fields).toContain("Inputs sourced directly from MSMEs and small producers, FY 2025-26: Restated in the FY 2026-27 BRSR to 9.9%");
  });

  it("shows the top five related-party sales and purchases in crore, as filed, with the period and the basis", () => {
    const g = text(panel(out, "panel-vc-group"));
    expect(g).toContain("Transactions M&M itself entered into; its subsidiaries' aren't included");
    expect(g).toContain("Mahindra Defence Systems Ltd.");
    expect(g).toContain("₹4,200 cr");
    expect(g).toContain("Mahindra Last Mile Mobility Ltd.");
    expect(g).toContain("Standalone, from the results filings");
    expect(g).toContain("From the half-yearly related-party disclosures in the results filings");
    expect(g).toContain("Source: NSE");
    expect(g).not.toContain("part of the year");
  });

  it("lists the group companies with the share held as filed", () => {
    const g = text(panel(out, "panel-vc-group"));
    expect(g).toContain("Mahindra Lifespace Developers Ltd.");
    expect(g).toContain("51.25%");
    expect(g).toContain("66.9%");
  });

  it("shows the segments with each one's share, consolidated", () => {
    const s = text(panel(out, "panel-vc-segments"));
    expect(s).toContain("Automotive");
    expect(s).toContain("₹60,000 cr");
    expect(s).toContain("70.0%");
    expect(s).toContain("consolidated");
  });

  it("shows the peers in a name order with their boundaries, the median only from three peers, and no ranking", () => {
    const peers = panel(out, "panel-vc-peers");
    const t2 = text(peers);
    expect(t2).toContain("Median of the other peers on this company's boundary");
    expect(t2).toContain("80.5%");
    expect(peers).not.toContain('aria-sort="ascending"');
    expect(peers).not.toContain("<button"); // no column sorts, so nothing ranks
    const few = text(panel(sub(pageData({ peers: pageData().peers?.map((r) => ({ ...r, median: null, median_n: 2 })) ?? null })), "panel-vc-peers"));
    expect(few).not.toContain("Median of the other peers");
  });

  it("states the source and the caveat on the figures", () => {
    expect(t).toContain(AS_REPORTED);
    expect(t).toContain("Source: the company's BRSR, filed on NSE");
    expect(t).toContain("tagged in the BRSR's XBRL file");
    expect(t).toContain(RESTATED_NOTE);
  });

  it("uses none of the banned words in its own text", () => {
    const own = t.replace(/Mahindra [A-Za-z&. ]+? (Ltd\.|Limited)/g, "").replace(/Automotive|Farm equipment|Financial services/g, "");
    expect(bannedValueChainWordsIn(own)).toEqual([]);
  });
});

describe("related parties: not available is not none", () => {
  it("a company with no related-party rows shows the not-available state in both lists, never an empty list or a 'no related parties' line", () => {
    const g = text(panel(sub(pageData({ rpt: [] })), "panel-vc-group"));
    expect(g).toContain("Not available: no sales related-party rows were read from this company's results filings for the latest year.");
    expect(g).toContain("Not available: no purchase related-party rows were read from this company's results filings for the latest year.");
    expect(g.toLowerCase()).not.toContain("no related parties");
    expect(g.toLowerCase()).not.toContain("none");
  });

  it("a six-month sum shows the period it covers beside the fiscal-year label and is not called a year", () => {
    const half = [
      rpt("Sale of goods or services", 1, "Coal Counterparty Ltd.", 1e10, { period_from: "2025-04-01", period_to: "2025-09-30" }),
      rpt("Purchase of goods or services", 1, "Coal Supplier Ltd.", 2e10, { period_from: "2025-04-01", period_to: "2025-09-30" }),
    ];
    const g = text(panel(sub(pageData({ rpt: half })), "panel-vc-group"));
    expect(g).toContain("FY 2025-26 (part of the year): covers 1 Apr 2025 – 30 Sep 2025, not the whole fiscal year.");
  });

  it("a failed related-party read says it couldn't load", () => {
    expect(text(panel(sub(pageData({ rpt: null })), "panel-vc-group"))).toContain("Couldn't load this panel");
  });
});

describe("segments and peers states", () => {
  it("a single-segment filer says so, as filed", () => {
    const s = text(panel(sub(pageData({ segments: [segment(1, "All", 1e12, null, { reporting: "single", state: "single_segment" })] })), "panel-vc-segments"));
    expect(s).toContain("One reportable segment, as filed.");
  });
  it("a company with no segment rows is not available", () => {
    expect(text(panel(sub(pageData({ segments: [] })), "panel-vc-segments"))).toContain("Not available: no segment revenue was read");
  });
  it("peers with the peer-group policy hidden are held back", () => {
    const only = ALL_SHOWN.filter((p) => p.field_key !== "equity.peer_group");
    expect(text(panel(sub(pageData({ policies: only })), "panel-vc-peers"))).toContain("Peer comparison held back until the value chain passes review");
  });
});
