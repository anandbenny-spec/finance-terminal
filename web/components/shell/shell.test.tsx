// The shell's tape, status bar and menu rendered to HTML as the server sends them (C2, #147): what a visitor (and a
// screen reader) gets before any script runs, in every state, so no part of the shell is ever blank.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MobileNav } from "@/components/shell/MobileNav";
import { NavList } from "@/components/shell/Nav";
import { Sidebar } from "@/components/shell/Sidebar";
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

describe("Sidebar (#147)", () => {
  const out = html(<Sidebar />);
  const navTag = /<nav [^>]*>/.exec(out)![0];

  it("keeps the column's panel colour and rule for the page's full height; only its contents stick", () => {
    expect(navTag).toContain('aria-label="Main"');
    expect(navTag).toContain("w-(--sidebar-w)");
    expect(navTag).toContain("bg-panel");
    expect(navTag).toContain("border-r");
    expect(navTag).toContain("lg:block");
    expect(navTag).not.toContain("sticky");
    // the contents: below the top bar, no taller than the window less the top and status bars, scrolling inside
    expect(out).toContain("sticky top-(--topbar-h) max-h-(--nav-max-h) overflow-y-auto");
  });

  it("is one expanded sidebar from lg: no rail classes, no tooltips", () => {
    expect(out).not.toContain("max-xl");
    expect(out).not.toContain("rail");
    expect(out).not.toContain("xl:w-");
    expect(out).not.toContain('data-slot="tooltip');
    expect(text(out)).toContain("Fed vs RBI");
    expect(text(out)).toContain("About and privacy");
  });

  it("nests Fed vs RBI's five section links in a list inside its item, 28 px rows with no icon", () => {
    const nested = /Fed vs RBI<\/span><\/a><ul[^>]*>([\s\S]*?)<\/ul><\/li>/.exec(out);
    expect(nested).not.toBeNull();
    const links = [...nested![1].matchAll(/<a [^>]*href="([^"]+)"[^>]*>([^<]*)<\/a>/g)].map((m) => [m[1], m[2]]);
    expect(links).toEqual([
      ["/policy#where-rates-stand", "Where rates stand"],
      ["/policy#latest-decisions", "Latest decisions"],
      ["/policy#history", "History"],
      ["/policy/fed/latest?tab=changes", "FOMC: what changed"],
      ["/policy/rbi/latest?tab=changes", "MPC: what changed"],
    ]);
    expect(nested![1]).not.toContain("<svg");
    expect(nested![1].match(/ h-7 /g)).toHaveLength(5);
    expect(nested![1].match(/ pl-9\.5 /g)).toHaveLength(5); // the item's border, padding, icon and gap: labels line up
    expect(nested![1]).not.toContain("aria-current");
  });

  it("marks only the top-level item current, never a section link", () => {
    const current = html(<NavList active="/policy" />);
    expect(current.match(/aria-current="page"/g)).toHaveLength(1);
    expect(/aria-current="page"[^>]*>(?:<svg[\s\S]*?<\/svg>)?<span>Fed vs RBI/.test(current)).toBe(true);
  });

  it("shows a visitor no admin group (the admin context decides; nav.test.ts covers the admin's list)", () => {
    expect(text(html(<NavList active={null} />))).not.toContain("Labelling");
  });
});

describe("MobileNav (#147)", () => {
  const out = html(<MobileNav />);

  it("is a labelled Menu button: the icon and the word, and the word is its accessible name", () => {
    expect(text(out)).toBe("Menu");
    expect(out).not.toContain("Open menu");
    expect(out).not.toContain("sr-only");
    expect(out).toMatch(/<svg[^>]*aria-hidden="true"/);
    expect(out).toContain('aria-haspopup="dialog"');
    expect(out).toContain('aria-expanded="false"');
  });

  it("is the outline Button, 32 px high with a line-input border, and only below lg", () => {
    expect(out).toContain('data-variant="outline"');
    expect(out).toContain("h-8");
    expect(out).toContain("border-line-input");
    expect(out).not.toContain("border-border");
    expect(out).toContain("lg:hidden");
  });

  it("lists the same links in the drawer, section links included (the drawer renders NavList)", () => {
    const drawer = html(<NavList active={null} onNavigate={() => {}} />);
    expect(drawer).toContain('href="/policy#history"');
    expect(drawer).toContain('href="/policy/rbi/latest?tab=changes"');
  });
});
