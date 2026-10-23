// The shell's tape and status bar rendered to HTML as the server sends them (C2): what a visitor (and a screen
// reader) gets before any script runs, in every state, so no part of the shell is ever blank.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatusBarView } from "@/components/shell/StatusBar";
import { TapeView } from "@/components/shell/TickerTape";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { SeriesLatestRow, SeriesRow } from "@/lib/queries/api";
import { summarise } from "@/lib/shell/freshness";
import { toUpcoming } from "@/lib/shell/meetings";
import { buildTape } from "@/lib/shell/tape";

const html = (node: ReactNode) => renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();

const SERIES: SeriesRow[] = [
  { key: "yf.^GSPC", name: "S&P 500", market: "US", attribution: "Source: S&P Dow Jones Indices LLC, via Yahoo Finance · © S&P Dow Jones Indices LLC" },
  { key: "fred.INDIRLTLT01STM", name: "India 10-year government bond yield (monthly)", market: "IN", attribution: "Source: OECD, via FRED" },
];
const LATEST: SeriesLatestRow[] = [
  { series_key: "yf.^GSPC", name: "S&P 500", unit: "index_points", frequency: "D", last_date: "2026-10-23", last_value: 6812.4, prev_date: "2026-10-22", prev_value: 6824.68, change: -12.28 },
  { series_key: "fred.INDIRLTLT01STM", name: "India 10Y", unit: "percent", frequency: "M", last_date: "2026-09-01", last_value: 6.41, prev_date: "2026-08-01", prev_value: 6.38, change: 0.03 },
];

describe("TapeView", () => {
  const items = buildTape(LATEST, SERIES);

  it("starts with the EOD tag and lists each item with its value and change", () => {
    const out = html(<TapeView items={items} delayed={false} />);
    expect(text(out)).toMatch(/^EOD S&P 500 6,812.40 −0.18%/);
    expect(out).toContain('aria-label="End-of-day market values"');
    expect(text(out)).toContain("IN 10Y 6.41% Sep 2026 monthly");
  });

  it("carries the date, source and copyright line for screen readers", () => {
    const out = html(<TapeView items={items} delayed={false} />);
    expect(out).toContain("down 0.18 percent, 23 Oct 2026. Source: S&amp;P Dow Jones Indices LLC, via Yahoo Finance. © S&amp;P Dow Jones Indices LLC.");
  });

  it("makes every item focusable, so its tooltip opens from the keyboard", () => {
    const out = html(<TapeView items={items} delayed={false} />);
    expect(out.match(/tabindex="0"/g)).toHaveLength(items.length + 1); // the items and the EOD tag
  });

  it("says when its data is behind", () => {
    expect(text(html(<TapeView items={items} delayed />))).toContain("EOD Update delayed S&P 500");
  });

  it("is never blank: nothing published yet, or a failed read", () => {
    expect(text(html(<TapeView items={[]} delayed />))).toBe("EOD No end-of-day values published yet.");
    expect(text(html(<TapeView items={null} delayed={false} />))).toBe("EOD Couldn't load market values.");
  });
});

describe("StatusBarView", () => {
  const meetings = toUpcoming([
    { bank: "Fed", slug: "fomc-2026-12-09", decision_date: "2026-12-09", decision_at: "2026-12-09T19:00:00+00:00" },
    { bank: "RBI", slug: "mpc-2026-12-04", decision_date: "2026-12-04", decision_at: "2026-12-04T04:30:00+00:00" },
  ]);

  it("sends the freshness summary and each bank's next date (the countdown is added in the browser)", () => {
    const summary = summarise(
      [
        { task_key: "policy.calendar", status: "ok", last_success_at: "2026-10-20T01:11Z", last_checked_at: null },
        { task_key: "policy.market_series", status: "ok", last_success_at: "2026-10-23T01:11Z", last_checked_at: null },
      ],
      ["policy.calendar", "policy.market_series"],
      "2026-10-24T00:00Z",
    );
    expect(text(html(<StatusBarView summary={summary} meetings={meetings} />))).toBe(
      "All data current · checked 20 Oct, 06:41 IST Next FOMC 9 Dec 2026 Next MPC 4 Dec 2026 Not investment advice",
    );
  });

  it("shows a stale task as stale (Review 01, F17)", () => {
    const summary = summarise([], ["policy.market_series"], "2026-10-24T00:00Z");
    expect(text(html(<StatusBarView summary={summary} meetings={[]} />))).toBe("1 source stale Not investment advice");
  });

  it("is never blank when its reads fail", () => {
    expect(text(html(<StatusBarView summary={null} meetings={null} />))).toBe(
      "Freshness unavailable Meeting dates unavailable Not investment advice",
    );
  });
});
