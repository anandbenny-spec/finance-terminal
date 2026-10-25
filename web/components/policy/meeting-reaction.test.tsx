// The Market reaction tab rendered to HTML as the server sends it (Pass 1b, #75; Spec 01 §4.3, §4.5): the table's
// moves (signed, with a triangle), the surprise-measure heading, one chart panel per series with "View as table", and
// a StateNote for what has no move (no panel blank). Test rows, not real figures.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MeetingReactionView } from "@/components/policy/MeetingReaction";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SURPRISE_TITLE, meetingReaction, shiftDate } from "@/lib/policy/reaction";
import { REACTION_SERIES, WAITING } from "@/lib/policy/reactions";
import type { MarketReactionRow, ObservationRow, SeriesRow } from "@/lib/queries/api";

const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const ALL = REACTION_SERIES.map((s) => s.key);
const series: SeriesRow[] = ALL.map((key) => ({ key, name: key, market: null, attribution: `Source: ${key}` }));
const obs = (key: string): ObservationRow[] =>
  Array.from({ length: 40 }, (_, i) => ({ series_key: key, obs_date: shiftDate("2024-08-25", i), value: 3 + i / 100, label: null }));
const row = (key: string, change: number, unit: "bps" | "pct"): MarketReactionRow => ({
  meeting_slug: "fomc-2024-09-18", series_key: key, series_name: key === "fred.DGS2" ? "US 2-year Treasury yield" : key,
  pre_obs_date: "2024-09-17", post_obs_date: "2024-09-18", pre_value: 3.61, post_value: 3.52, change, change_unit: unit,
  is_surprise_measure: key === "fred.DGS2", attribution: null,
});

function render(reactions: MarketReactionRow[], today = "2024-09-20") {
  const model = meetingReaction({
    bank: "Fed", decisionDate: "2024-09-18", today, reactions, series,
    observations: Object.fromEntries(ALL.map((k) => [k, obs(k)])),
  });
  return renderToStaticMarkup(
    <TooltipProvider>
      <MeetingReactionView model={model} footer={<p>Footer</p>} />
    </TooltipProvider>,
  );
}

describe("MeetingReactionView", () => {
  it("shows each move signed in its table, headed with the surprise measure first", () => {
    const html = render([row("fred.DGS2", -9, "bps"), row("yf.^GSPC", 0.5, "pct")]);
    const t = text(html);
    expect(t).toContain(SURPRISE_TITLE);
    expect(t).toContain("−9");
    expect(t).toContain("+0.50");
    expect(t).toContain("17 Sep 2024 3.61%");
    expect(t.indexOf(SURPRISE_TITLE)).toBeLessThan(t.indexOf("yf.^GSPC"));
    expect(t).toContain("Derived:");
  });

  it("draws one chart panel per series with a move, each with View as table, and nothing blank for the others", () => {
    const html = render([row("fred.DGS2", -9, "bps"), row("yf.^NSEI", 0.1, "pct")]);
    expect(html.match(/aria-label="View as table"/g)).toHaveLength(2);
    expect(text(html)).toContain(WAITING);
  });

  it("calls a move that is long overdue not available rather than waiting", () => {
    const t = text(render([], "2025-03-01"));
    expect(t).not.toContain(WAITING);
    expect(t).toContain("Not available.");
  });

  it("carries the footer on the table panel and every chart panel", () => {
    const html = render([row("fred.DGS2", -9, "bps")]);
    expect(html.match(/Footer/g)).toHaveLength(2);
  });
});
