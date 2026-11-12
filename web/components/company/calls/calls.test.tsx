// The Earnings calls tab rendered to HTML as the server sends it (2B-19, #182; Spec 02B §8, §13.3, §13.4, §13.7; 02B.1 C6):
// the calls table and the default call, the summary's lists with chips, the three figure tables and their states, the page
// links ("p. 7 ↗"), the private-text popover, the role-held call's label and extra panel, no colour on AI figures, no
// transcript text, tokens only, and the banned-words scan. Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AIBlock } from "@/components/ai/AIBlock";
import { Passage } from "@/components/ai/CitationChip";
import { PRIVATE_TEXT_NOTE, isPrivateText } from "@/components/policy/DocumentPage";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SUMMARY_HELD, SUMMARY_LABEL, callOf, figureText, selectCall, statusText, summaryLists } from "@/lib/equity/calls";
import { tabOf } from "@/lib/equity/company";
import { formatPageRange, formatStatedValue } from "@/lib/format";
import { toCitation, wholeQuote } from "@/lib/policy/ai";
import type { CallsTabData } from "@/lib/queries/calls";
import { bannedCallWordsIn, BANNED_WORDS, CALLS_BANNED_WORDS } from "@/tests/banned-words";
import { HELD, call, calls, citation, figure, output, pageRow } from "@/tests/call-rows";
import { page } from "@/tests/company-rows";

const text = (markup: string) =>
  markup.replace(/<span class="sr-only">[^<]*<\/span>/g, "").replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const html = (c: CallsTabData | null) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={page({ tab: "calls", overview: null, calls: c })} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );

/** The tab's markup with every blockquote taken out: what is left must hold none of a transcript's words (#371). */
const withoutBlockquotes = (markup: string) => markup.replace(/<blockquote[\s\S]*?<\/blockquote>/g, "");
/** The text of each blockquote, in page order. */
const quotesIn = (markup: string) => [...markup.matchAll(/<blockquote[^>]*>([\s\S]*?)<\/blockquote>/g)].map((m) => text(m[1]));
/** The summary panel's text: from its heading to the next panel. */
const summaryPanel = (markup: string) => {
  const t = text(markup);
  return t.slice(t.indexOf("call: what was said"), t.indexOf("Guidance given on this call"));
};

describe("the guided tour's target (#91)", () => {
  it("is the calls panel, drawn with a call and without one, so the stop never lands on a held-back panel", () => {
    expect(html(calls())).toContain('data-tour="company-calls"');
    expect(html(calls({ calls: [], selected: null }))).toContain('data-tour="company-calls"');
  });
});

describe("the tab and the call in the URL", () => {
  it("is a tab in the URL, marked current", () => {
    expect(tabOf("calls")).toBe("calls");
    const out = html(calls());
    expect(out).toContain('href="/companies/tcs/calls"');
    expect(out).toMatch(/aria-current="page"[^>]*>Earnings calls</);
  });

  it("opens on the default call and selects another with /calls/<call>", () => {
    const old = call({ call_slug: "q1-fy27", fiscal_label: "Q1 FY27", period_end: "2026-06-30", is_default: false, is_newest: false, status: "on_company_website" });
    const now = call();
    expect(selectCall([old, now], undefined)?.call_slug).toBe("q2-fy27");
    expect(selectCall([old, now], "q1-fy27")?.call_slug).toBe("q1-fy27");
    expect(selectCall([old, now], "nonsense")?.call_slug).toBe("q2-fy27");
    expect(callOf("q1-fy27")).toBe("q1-fy27");
    expect(callOf("../x")).toBeUndefined();
    const out = html(calls({ calls: [old, now], selected: old }));
    expect(out).toContain('href="/companies/tcs/calls/q1-fy27"');
    expect(out).toContain('href="/companies/tcs/calls/q2-fy27"');
    expect(text(out)).toContain("Q1 FY27 call: what was said");
    expect(text(out)).toContain("Transcript on the company's website");
  });

  it("lists the calls newest first with the call date, or the day the transcript was filed when the date is unknown", () => {
    const t = text(html(calls({ calls: [call({ call_slug: "q1-fy27", fiscal_label: "Q1 FY27", period_end: "2026-06-30", is_default: false, call_date: null, filed_at: "2026-07-21T09:00:00+05:30" }), call()] })));
    expect(t.indexOf("Q2 FY27")).toBeLessThan(t.indexOf("Q1 FY27 "));
    expect(t).toContain("9 Oct 2026");
    expect(t).toContain("Transcript filed on 21 Jul 2026");
  });
});

describe("the summary", () => {
  it("shows What management said and What analysts asked about, each bullet with its chip", () => {
    const out = html(calls());
    const t = text(out);
    expect(t).toContain("What management said");
    expect(t).toContain("What analysts asked about");
    expect(t).toContain("Management described demand as steady across regions.");
    expect(out).toContain("Source 1: show the cited passage");
    expect(out).toContain("Source 2: show the cited passage");
    expect(t).not.toContain("Said by participants whose role couldn't be read Not attributed");
  });

  it("drops a bullet with no verified citation and shows quote text only inside a blockquote", () => {
    const lists = summaryLists(output(), [citation(1)], []);
    expect(lists.flatMap((l) => l.bullets.map((b) => b.text))).toEqual(["Management described demand as steady across regions."]);
    // Changed with Anand's OK (10 Nov 2026, on #371; CLAUDE.md §7). This used to say the quote text shows nowhere outside a chip's
    // popover. The quote-led summary now shows each verified quote under its bullet, so the rule is where the words may appear:
    // only in a blockquote under a bullet, never anywhere else on the tab.
    expect(text(withoutBlockquotes(html(calls())))).not.toContain("Quoted words number 1");
    expect(quotesIn(html(calls()))).toEqual(["Quoted words number 1.", "Quoted words number 2."]);
  });

  it("says the summary is held back while its field is hidden, and not generated yet when the call has none", () => {
    expect(text(html(calls({ policies: HELD, summary: null, calls: [call({ summary_state: "held_back", summary_output_id: null })], selected: call({ summary_state: "held_back", summary_output_id: null }) })))).toContain(
      "AI summary held back until it passes review",
    );
    const none = call({ summary_state: "not_generated", summary_output_id: null, figures_output_id: null, target_count: null, stated_figure_count: null });
    const t = text(html(calls({ calls: [none], selected: none, summary: null, figures: [] })));
    expect(t).toContain("Summary not generated yet");
    expect(t).toContain("Figures not generated yet.");
  });

  it("says why a call with no analysable transcript has no summary, in the status words", () => {
    const web = call({ status: "on_company_website", summary_state: "not_generated", summary_output_id: null, figures_output_id: null });
    const scan = call({ status: "unreadable", summary_state: "not_generated", summary_output_id: null, figures_output_id: null });
    expect(text(html(calls({ calls: [web], selected: web, summary: null, figures: [] })))).toContain("Transcript on the company's website");
    const t = text(html(calls({ calls: [scan], selected: scan, summary: null, figures: [] })));
    expect(t).toContain("Transcript is a scanned image; not analysed");
    expect(t).not.toContain("Guidance given on this call");
  });

  it("says No transcript found with the window the discovery covered", () => {
    const t = text(html(calls({ calls: [], selected: null, summary: null, figures: [], latest: { company_slug: "tcs", status: "none_found", window_from: "2026-07-01", window_to: "2026-11-05" } })));
    expect(t).toContain("No transcript found between 1 Jul 2026 and 5 Nov 2026.");
  });
});

describe("the figures", () => {
  it("lists guidance with the figure as stated, the period, basis, a chip and the page link; labelled AI-extracted", () => {
    const out = html(calls());
    const t = text(out);
    expect(t).toContain("Guidance given on this call");
    expect(t).toContain("EBIT margin 26% to 28% FY27 Consolidated");
    expect(t).toContain("AI-extracted");
    expect(out).toContain('href="https://example.test/tcs-q2.pdf#page=7"');
    expect(out).toContain('title="Page 7 of the PDF at the source"');
    expect(t).toContain("p. 7 ↗");
    // The per-call panels carry no outcome or hit rate (Plan 04b P12); the Guidance record panel after them does (guidance-record.test.tsx).
    expect(t.slice(0, t.indexOf("Guidance record"))).not.toMatch(/outcome|not scored|hit rate/i);
  });

  it("lists the figures management stated, with the line saying so", () => {
    const t = text(html(calls()));
    expect(t).toContain("Figures management stated");
    expect(t).toContain("As stated by management on the call");
    expect(t).toContain("EBIT margin 24.5% FY27 Consolidated");
    expect(t).toContain("p. 8 ↗");
  });

  it("says no measurable targets were found, and shows no page where the view has none", () => {
    const c = calls({ figures: [figure("stated", { page_first: null, page_last: null, page_url: null })], pages: [] });
    const out = html(c);
    expect(text(out)).toContain("No measurable targets found on this call");
    expect(text(out)).not.toContain("p. 8 ↗");
    expect(out).not.toContain("#page=");
  });

  it("holds the figures back while their field is hidden", () => {
    const t = text(html(calls({ policies: [{ field_key: "ai.call_summary", status: "labelled", label: "AI summary" }, ...HELD.slice(1)], figures: [] })));
    expect(t).toContain("AI-extracted figures held back until they pass review");
    expect(t).not.toContain("No measurable targets");
  });

  it("writes each figure as the quote does: a floor, a ceiling, a direction, a phrase", () => {
    const base = figure("target");
    expect(figureText({ ...base, figure_kind: "floor", value: 10, low: null, high: null })).toBe("At least 10%");
    expect(figureText({ ...base, figure_kind: "ceiling", value: 0, low: null, high: null, unit: "inr" })).toBe("At most ₹0 cr");
    expect(figureText({ ...base, figure_kind: "direction", value: null, low: null, high: null, direction: "increase", comparison: "year_ago", growth_basis: null })).toBe("Increase against a year ago");
    expect(figureText({ ...base, figure_kind: "point", value: null, low: null, high: null })).toBe("Stated in words (see the quote)");
    expect(formatStatedValue(4500, "inr")).toBe("₹4,500 cr");
    expect(formatStatedValue(1234567.5, "inr")).toBe("₹12,34,567.5 cr");
    expect(formatStatedValue(12.5, "pct")).toBe("12.5%");
    expect(formatStatedValue(-2, "pct")).toBe("−2%");
  });
});

describe("the quote-led summary (#371)", () => {
  const long = Array.from({ length: 50 }, (_, i) => `word${i}`).join(" ");
  const two = output({
    content: { bullets: [{ text: "Management gave two reasons for the margin.", citations: [2, 1], list: "management" }] },
  });

  it("puts each bullet's quote directly under it, after its chip, with the PDF page link where there is one", () => {
    const s = summaryPanel(html(calls()));
    expect(s).toContain("Management described demand as steady across regions. [1] Quoted words number 1. p. 3 ↗ What analysts asked about");
    expect(s).toContain("Analysts asked about hiring plans. [2] Quoted words number 2. pp. 4–5 ↗");
  });

  it("shows two quotes for a bullet with two citations, in the order its chips run", () => {
    const s = summaryPanel(html(calls({ summary: two })));
    expect(s).toContain("[2] [1] Quoted words number 2. pp. 4–5 ↗ Quoted words number 1. p. 3 ↗");
    expect(quotesIn(html(calls({ summary: two })))).toEqual(["Quoted words number 2.", "Quoted words number 1."]);
  });

  it("labels each block once, in the note slot, with Anand's wording", () => {
    expect(SUMMARY_LABEL).toBe("AI paraphrase; the quote is the speaker's words.");
    const s = summaryPanel(html(calls()));
    expect(s.split(SUMMARY_LABEL)).toHaveLength(3); // two blocks, two labels
    expect(s.indexOf(SUMMARY_LABEL)).toBeLessThan(s.indexOf("Management described demand"));
  });

  it("shows the whole stored quote, uncut, while the chip's popover keeps its cut", () => {
    expect([...long].length).toBeGreaterThan(280);
    expect([...long].length).toBeLessThanOrEqual(400);
    const d = calls({ citations: [citation(1, { quote: `  ${long}\n` }), citation(2)] });
    expect(quotesIn(html(d))[0]).toBe(long);
    const cited = summaryLists(output(), d.citations, [])[0].bullets[0].citations[0];
    expect(cited.fullQuote).toBe(long);
    expect([...cited.quote].length).toBeLessThanOrEqual(280); // the chip's popover is unchanged
    expect(wholeQuote("  a\n  b ")).toBe("a b");
  });

  it("still drops a bullet whose citation row is missing, and shows no quote for it", () => {
    const d = calls({ citations: [citation(1)] });
    const s = summaryPanel(html(d));
    expect(s).toContain("Management described demand as steady across regions. [1] Quoted words number 1.");
    expect(s).not.toContain("Analysts asked about hiring plans.");
    expect(s).not.toContain("Quoted words number 2.");
    expect(s).not.toContain("What analysts asked about");
  });

  it("shows neither bullets nor quotes while the field is hidden", () => {
    const s = summaryPanel(html(calls({ policies: HELD })));
    expect(s).toContain(SUMMARY_HELD);
    expect(s).not.toContain("Management described demand");
    expect(s).not.toContain("Quoted words");
    expect(html(calls({ policies: HELD }))).not.toContain("<blockquote");
  });

  it("gives the unread-role list the same treatment, with its own note first", () => {
    const held = call({ roles_partial: true, unknown_role_share: 0.52 });
    const d = calls({
      calls: [held],
      selected: held,
      summary: output({ content: { bullets: [{ text: "A participant said hiring would continue.", citations: [2], list: "unknown" }] } }),
    });
    const s = summaryPanel(html(d));
    expect(s).toContain(`Said by participants whose role couldn't be read Not attributed to management. ${SUMMARY_LABEL}`);
    expect(s).toContain("A participant said hiring would continue. [2] Quoted words number 2. pp. 4–5 ↗");
  });

  it("sets each quote as a blockquote inside its bullet's list item, at the passage's text size, with nothing that opens on hover", () => {
    const out = html(calls());
    const panel = out.slice(out.indexOf('aria-labelledby="panel-call-summary"'), out.indexOf('aria-labelledby="panel-call-guidance"'));
    const items = [...panel.matchAll(/<li>([\s\S]*?)<\/li>/g)].map((m) => m[1]);
    expect(items).toHaveLength(2);
    for (const li of items) expect(li).toMatch(/<\/button><\/span><\/span><div class="[^"]*"><div><blockquote class="[^"]*\btext-data\b[^"]*"/);
    expect(out).toMatch(/<blockquote class="border-l-2 border-highlight pl-2\.5 text-data text-ink-1 break-words">/); // the passage's rule and size
    expect(out).not.toMatch(/<blockquote[^>]*(onmouse|hover:)/i);
  });

  it("is off by default: a module 1 AIBlock without the prop shows its chips alone", () => {
    const block = { label: "AI summary", model: "claude-haiku-5-5", createdAt: "2026-10-30T09:00:00+00:00", bullets: [{ text: "The Committee held the rate.", citations: [toCitation(citation(1), true, pageRow(1, 3))!] }] };
    const render = (quotes?: boolean) =>
      renderToStaticMarkup(
        <TooltipProvider>
          <AIBlock block={block} now="2026-11-10T09:00:00+00:00" dense={false} quotes={quotes} />
        </TooltipProvider>,
      );
    expect(render()).not.toContain("<blockquote");
    expect(text(render())).not.toContain("Quoted words");
    expect(render(false)).not.toContain("<blockquote");
    // With the prop and no fullQuote, the cut quote shows: nothing breaks for a citation built elsewhere.
    expect(quotesIn(render(true))).toEqual(["Quoted words number 1."]);
  });
});

describe("a role-held call (Spec 02B.1 C6)", () => {
  const held = call({ roles_partial: true, unknown_role_share: 0.52, unattributed_targets: 1 });
  const data = calls({
    calls: [held],
    selected: held,
    summary: output({
      content: {
        bullets: [
          { text: "Management described demand as steady.", citations: [1], list: "management" },
          { text: "A participant said hiring would continue.", citations: [2], list: "unknown" },
        ],
      },
    }),
    figures: [figure("target"), figure("target", { speaker_role: "unknown", citation_no: 2, metric_label: "Revenue", figure_kind: "floor", value: 10, low: null, high: null })],
  });

  it("shows the partial label, the third list and the extra panel", () => {
    const t = text(html(data));
    expect(t).toContain("Partial: speaker roles couldn't be read for 52% of this call's words.");
    expect(t).toContain("Said by participants whose role couldn't be read Not attributed to management.");
    expect(t).toContain("Forward-looking figures from speakers whose role couldn't be read. Not attributed to management, not scored and not in any hit rate. AI-extracted.");
    expect(t).toContain("Revenue At least 10%");
    expect(t).toContain("roles partly read");
  });

  it("keeps an unattributed target out of the guidance table", () => {
    const t = text(html(data));
    const guidance = t.slice(t.indexOf("Guidance given on this call"), t.indexOf("Figures management stated"));
    expect(guidance).toContain("EBIT margin");
    expect(guidance).not.toContain("Revenue");
  });

  it("has its own empty states", () => {
    const empty = calls({ calls: [held], selected: held, figures: [] });
    const t = text(html(empty));
    expect(t).toContain("No measurable targets found in the turns attributed to management");
    expect(t).toContain("No figures found in the turns attributed to management");
    expect(t).toContain("No forward-looking figures found in the turns whose speaker couldn't be read");
  });

  it("is absent on a clean call", () => {
    const t = text(html(calls()));
    expect(t).not.toContain("Partial:");
    expect(t).not.toContain("Said by participants");
    expect(t).not.toContain("Forward-looking figures from speakers");
  });
});

describe("the citation popover on a private text (Spec 02B §8, §13.4)", () => {
  const cite = (row = citation(1), pages = pageRow(1, 7)) => toCitation(row, true, pages)!;

  it("has no Open in document, keeps Official source, and links the page", () => {
    const out = renderToStaticMarkup(<Passage citation={cite()} />);
    expect(out).not.toContain("Open in document");
    expect(out).toContain("Official source ↗");
    expect(out).toContain("p. 7 ↗");
    expect(out).toContain('title="Page 7 of the PDF at the source"');
    expect(out).toContain("Quoted words number 1.");
  });

  it("names both pages of a span across a break", () => {
    const out = renderToStaticMarkup(<Passage citation={cite(citation(1), pageRow(1, 7, 8))} />);
    expect(out).toContain("pp. 7–8 ↗");
    expect(formatPageRange(7, 8)).toBe("pp. 7–8");
  });

  it("shows no page without a row, and never a page link at a host that isn't https", () => {
    expect(renderToStaticMarkup(<Passage citation={toCitation(citation(1), true, null)!} />)).not.toContain("p. ");
    expect(toCitation(citation(1), true, pageRow(1, 7, 7, { page_url: "http://x.test/a.pdf#page=7" }))!.page).toBeNull();
  });

  it("keeps Open in document for a public text (module 1)", () => {
    const out = renderToStaticMarkup(<Passage citation={toCitation(citation(1, { text_public: true }), false)!} />);
    expect(out).toContain("Open in document");
  });

  it("treats the transcript types as private text on /documents/[id]", () => {
    expect(isPrivateText({ doc_type: "call_transcript" })).toBe(true);
    expect(isPrivateText({ doc_type: "fed_statement" })).toBe(false);
    expect(PRIVATE_TEXT_NOTE).toBe("This transcript isn't reproduced here. Read it at the source.");
  });
});

describe("what the tab promises", () => {
  const whole = html(calls({ calls: [call({ roles_partial: true, unknown_role_share: 0.5 })], selected: call({ roles_partial: true, unknown_role_share: 0.5 }) }));
  // The tab alone: the company header above it shows the day's price move, which is a market move and is coloured.
  const out = whole.slice(whole.indexOf('aria-labelledby="panel-calls"'));

  it("puts no market colour on any AI figure or call status", () => {
    expect(out).not.toMatch(/text-(up|down|flat)\b/);
    expect(out).not.toMatch(/text-(red|green)|#[0-9a-f]{3,6}\b|rgb\(/i);
  });

  it("carries no market edge, a source line and a freshness on every panel", () => {
    expect(out).not.toMatch(/border-t-series/);
    expect(text(out)).toContain("Source: NSE");
    expect((out.match(/<section/g) ?? []).length).toBeGreaterThanOrEqual(4);
  });

  it("uses none of the words the site never uses (§8's list, and 2A's except for the panel's own word)", () => {
    expect(bannedCallWordsIn(text(out))).toEqual([]);
    for (const w of ["credible", "credibility", "trust", "trustworthy", "reliable", "unreliable", "misled", "misleading", "broke", "promise"]) {
      expect(CALLS_BANNED_WORDS.test(w)).toBe(true);
      expect(BANNED_WORDS.test(w)).toBe(true);
    }
    for (const status of ["stored", "on_company_website", "unreadable", "held"]) {
      for (const state of ["available", "held_back", "not_generated"] as const) {
        expect(bannedCallWordsIn(statusText({ status, summary_state: state, roles_partial: true }))).toEqual([]);
      }
    }
  });

  it("shows a transcript's words only as a verified citation's quote: under its summary bullet, and in no table, heading or note", () => {
    // Changed with Anand's OK (10 Nov 2026, on #371; CLAUDE.md §7). This used to say "Quoted words" is nowhere in the tab (the
    // quotes lived in a chip's popover, closed in a static render). The summary's quotes now sit under their bullets in a
    // blockquote; every other place still shows a chip alone, so the figures' quotes stay out of the markup and the blockquotes
    // are exactly the summary's.
    expect(withoutBlockquotes(out)).not.toContain("Quoted words");
    expect(out).not.toContain("Margin words");
    expect(out).not.toContain("Stated words");
    expect(quotesIn(out)).toHaveLength(2);
  });
});
