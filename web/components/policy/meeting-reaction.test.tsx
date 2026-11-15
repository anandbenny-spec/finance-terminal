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

function render(reactions: MarketReactionRow[], today = "2024-09-20", own = false, attributions: SeriesRow[] = series) {
  const model = meetingReaction({
    bank: "Fed", decisionDate: "2024-09-18", today, reactions, series: attributions,
    observations: Object.fromEntries(ALL.map((k) => [k, obs(k)])),
  });
  return renderToStaticMarkup(
    <TooltipProvider>
      <MeetingReactionView
        model={model}
        footer={<p>Footer all: {model.sources.filter(Boolean).join(" | ")}</p>}
        chartFooters={own ? Object.fromEntries(model.entries.filter((e) => e.source !== null).map((e) => [e.source as string, <p key={e.key}>Footer own: {e.source}</p>])) : undefined}
      />
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

  it("gives each chart its own series' source, and the table panel every shown series' (R05-8)", () => {
    const html = render([row("fred.DGS2", -9, "bps"), row("yf.^GSPC", 0.5, "pct")], "2024-09-20", true);
    const own = html.match(/Footer own: [^<]*/g) ?? [];
    expect(own.map((x) => x.replace(/<!-- -->/g, ""))).toEqual(expect.arrayContaining(["Footer own: Source: fred.DGS2", "Footer own: Source: yf.^GSPC"]));
    expect(own).toHaveLength(2);
    expect(html).not.toMatch(/Footer own: [^<]*yf\.\^NSEI/);
    expect(html).toContain("Footer all:");
    expect(text(html)).toContain("Footer all: Source: fred.DGS2 | Source: fred.DGS10 | Source: yf.^GSPC | Source: fred.DEXINUS | Source: yf.^NSEI | Source: yf.^NSEBANK");
  });

  it("shows an index series' copyright line under its own chart (R05-8)", () => {
    const attributed = series.map((s) => (s.key === "yf.^GSPC" ? { ...s, attribution: "Source: Yahoo Finance · S&P Dow Jones Indices LLC" } : s));
    const html = render([row("fred.DGS2", -9, "bps"), row("yf.^GSPC", 0.5, "pct")], "2024-09-20", true, attributed);
    expect(html).toContain("Footer own: Source: Yahoo Finance · S&amp;P Dow Jones Indices LLC");
    expect(html.match(/Footer own: [^<]*DGS2[^<]*/)?.[0]).not.toContain("S&amp;P");
  });

  it("keeps the full footer on a chart whose series carries no attribution (R05-8)", () => {
    const bare = series.map((s) => (s.key === "fred.DGS2" ? { ...s, attribution: null } : s));
    const html = render([row("fred.DGS2", -9, "bps")], "2024-09-20", true, bare);
    expect(html).not.toContain("Footer own:");
    expect(html).toContain("Footer all:");
  });
  it("takes the charts' footers as nodes, never a function: a client component can't be handed one by the server page (12 Nov 2026's failed builds)", () => {
    const model = meetingReaction({ bank: "Fed", decisionDate: "2024-09-18", today: "2024-09-20", reactions: [row("fred.DGS2", -9, "bps")], series, observations: Object.fromEntries(ALL.map((k) => [k, obs(k)])) });
    // @ts-expect-error a function is not a map of nodes
    const bad = <MeetingReactionView model={model} footer={null} chartFooters={(source: string) => source} />;
    expect(bad).toBeTruthy();
    const html = renderToStaticMarkup(
      <TooltipProvider>
        <MeetingReactionView model={model} footer={<p>Footer all</p>} chartFooters={{ "Source: fred.DGS2": <p>Footer own: DGS2</p>, constructor: <p>never</p> }} />
      </TooltipProvider>,
    );
    expect(html).toContain("Footer own: DGS2");
    expect(html).not.toContain("never");
  });
});
