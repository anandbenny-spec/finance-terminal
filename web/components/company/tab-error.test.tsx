// A company-page tab whose data is null (its reads failed, #328) says so in its own body: the header and the tab strip
// still render, and the body is a StateNote of kind "error", never blank (CLAUDE.md §5). The guidance record fails alone.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompanyView } from "@/components/company/CompanyView";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { TabId } from "@/lib/equity/company";
import { calls } from "@/tests/call-rows";
import { page } from "@/tests/company-rows";

const view = (over: Parameters<typeof page>[0]) =>
  renderToStaticMarkup(
    <TooltipProvider>
      <CompanyView data={page({ overview: null, ...over })} basis="consolidated" freshness={null} />
    </TooltipProvider>,
  );
const ERROR = 'data-state-kind="error"';
const SORRY = "Couldn&#x27;t load this panel. Try again.";

describe("a tab with no data", () => {
  it.each(["overview", "financials", "valuation", "rate-decisions", "calls"] as TabId[])("%s shows the error note and keeps the header and tabs", (tab) => {
    const html = view({ tab, tabFailed: true });
    expect(html).toContain(ERROR);
    expect(html).toContain(SORRY);
    expect(html).toContain(`aria-label="${page().company.name} sections"`);
    expect(html).toContain(page().company.name);
    for (const other of ["financials", "valuation", "rate-decisions", "calls"]) expect(html).toContain(`tab=${other}`);
  });
});

describe("the guidance record", () => {
  it("shows the error note in its own panel while the calls list and summary render", () => {
    const html = view({ tab: "calls", calls: calls({ guidance: null }) });
    const at = html.indexOf('id="panel-guidance-record"');
    expect(at).toBeGreaterThan(-1);
    expect(html.slice(at)).toContain(ERROR);
    expect(html).toContain('id="panel-calls"');
    expect(html).toContain('id="panel-call-summary"');
    expect(html.slice(0, at)).not.toContain(ERROR);
  });
});
