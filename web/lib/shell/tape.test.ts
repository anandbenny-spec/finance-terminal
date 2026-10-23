// The ticker tape's items (C2; Spec 01 §4.1): order, the published series only, each figure formatted by its kind,
// and the US indices' copyright lines (C2's done-when: "its US index items show their copyright line"). The rows
// are shaped like api.series_latest's and api.series's; the values are samples, not data.
import { describe, expect, it } from "vitest";
import type { SeriesLatestRow, SeriesRow } from "@/lib/queries/api";
import { buildTape, sourceLinesOf, TAPE } from "./tape";

function latest(series_key: string, last_value: number, prev_value: number | null, last_date = "2026-10-23"): SeriesLatestRow {
  return {
    series_key,
    name: series_key,
    unit: "",
    frequency: "D",
    last_date,
    last_value,
    prev_date: prev_value === null ? null : "2026-10-22",
    prev_value,
    change: prev_value === null ? null : last_value - prev_value,
  };
}

// Attributions as migrations 0008 and 0012 seed them.
const SERIES: SeriesRow[] = [
  { key: "yf.^NSEI", name: "Nifty 50", market: "IN", attribution: "Source: NSE Indices, via Yahoo Finance" },
  { key: "yf.^BSESN", name: "Sensex", market: "IN", attribution: "Source: BSE Index Services, via Yahoo Finance" },
  { key: "yf.^GSPC", name: "S&P 500", market: "US", attribution: "Source: S&P Dow Jones Indices LLC, via Yahoo Finance · © S&P Dow Jones Indices LLC" },
  { key: "yf.^IXIC", name: "Nasdaq Composite", market: "US", attribution: "Source: Nasdaq, Inc., via Yahoo Finance · © Nasdaq, Inc." },
  { key: "yf.^NDX", name: "Nasdaq-100", market: "US", attribution: "Source: Nasdaq, Inc., via Yahoo Finance · © Nasdaq, Inc." },
  { key: "yf.^DJI", name: "Dow Jones Industrial Average", market: "US", attribution: "Source: S&P Dow Jones Indices LLC, via Yahoo Finance · © S&P Dow Jones Indices LLC" },
  { key: "yf.INR=X", name: "USD/INR (Yahoo Finance)", market: "GLOBAL", attribution: "Source: Yahoo Finance" },
  { key: "fred.DGS10", name: "US 10-year Treasury yield", market: "US", attribution: "Source: U.S. Treasury, via Federal Reserve Board (H.15) and FRED" },
  { key: "fred.INDIRLTLT01STM", name: "India 10-year government bond yield (monthly)", market: "IN", attribution: "Source: OECD, via FRED" },
  { key: "fred.DEXINUS", name: "USD/INR", market: "GLOBAL", attribution: "Source: Federal Reserve Board (H.10), via FRED" },
];

const LATEST: SeriesLatestRow[] = [
  latest("yf.^NSEI", 25104.3, 24999.3),
  latest("yf.^BSESN", 123456.7, 123456.7),
  latest("yf.^GSPC", 6812.4, 6824.68),
  latest("yf.^IXIC", 21345.67, 21300),
  latest("yf.^NDX", 24000, 24100),
  latest("yf.^DJI", 46123.45, 46000),
  latest("yf.INR=X", 88.2731, 88.2731),
  latest("fred.DGS10", 4.08, 4.11, "2026-10-22"),
  latest("fred.INDIRLTLT01STM", 6.41, 6.38, "2026-09-01"),
  latest("fred.DEXINUS", 88.1, 88.0), // published, but not a tape item (D6: the tape's USD/INR is INR=X)
];

describe("buildTape", () => {
  const items = buildTape(LATEST, SERIES);
  const byKey = new Map(items.map((i) => [i.key, i]));

  it("keeps the config's order and only its series", () => {
    expect(items.map((i) => i.label)).toEqual(["Nifty 50", "Sensex", "S&P 500", "Nasdaq", "Nasdaq-100", "Dow", "USD/INR", "US 10Y", "IN 10Y"]);
    expect(byKey.has("fred.DEXINUS")).toBe(false);
  });

  it("formats each kind as Design 01 §3 sets it", () => {
    expect(byKey.get("yf.^NSEI")).toMatchObject({ value: "25,104.30", change: { text: "+0.42%", direction: "up" } });
    expect(byKey.get("yf.^BSESN")).toMatchObject({ value: "1,23,456.70", change: { text: "0.00%", direction: "flat" } });
    expect(byKey.get("yf.^GSPC")).toMatchObject({ value: "6,812.40", change: { text: "−0.18%", direction: "down" } });
    expect(byKey.get("yf.INR=X")).toMatchObject({ value: "88.27", change: { text: "0.00%", direction: "flat" } });
    expect(byKey.get("fred.DGS10")).toMatchObject({ value: "4.08%", change: { text: "−3 bps", direction: "down" } });
  });

  it("shows the India 10-year as monthly, with its month and no change", () => {
    expect(byKey.get("fred.INDIRLTLT01STM")).toMatchObject({
      value: "6.41%",
      change: null,
      month: "Sep 2026",
      qualifier: "monthly",
      date: "Sep 2026",
    });
  });

  it("gives each item its date and source line, and the US indices their copyright line", () => {
    expect(byKey.get("yf.^NSEI")).toMatchObject({ date: "23 Oct 2026", sourceLines: ["Source: NSE Indices, via Yahoo Finance"] });
    expect(byKey.get("fred.DGS10")?.date).toBe("22 Oct 2026");
    for (const [key, owner] of [
      ["yf.^GSPC", "© S&P Dow Jones Indices LLC"],
      ["yf.^IXIC", "© Nasdaq, Inc."],
      ["yf.^NDX", "© Nasdaq, Inc."],
      ["yf.^DJI", "© S&P Dow Jones Indices LLC"],
    ]) {
      expect(byKey.get(key)?.sourceLines).toEqual([expect.stringMatching(/^Source: /), owner]);
      expect(byKey.get(key)?.description).toContain(owner);
    }
  });

  it("says everything in one sentence for screen readers", () => {
    expect(byKey.get("yf.^GSPC")?.description).toBe(
      "S&P 500 (S&P 500): 6,812.40, down 0.18 percent, 23 Oct 2026. Source: S&P Dow Jones Indices LLC, via Yahoo Finance. © S&P Dow Jones Indices LLC.",
    );
    expect(byKey.get("fred.INDIRLTLT01STM")?.description).toBe(
      "IN 10Y (India 10-year government bond yield (monthly)): 6.41%, monthly, Sep 2026. Source: OECD, via FRED.",
    );
  });

  it("leaves out a series the view doesn't return (unpublished, or suppressed: RLS hides it)", () => {
    const tape = buildTape(
      LATEST.filter((r) => r.series_key !== "yf.^IXIC"),
      SERIES.filter((s) => s.key !== "yf.^NDX"),
    );
    expect(tape.map((i) => i.key)).not.toContain("yf.^IXIC");
    expect(tape.map((i) => i.key)).not.toContain("yf.^NDX");
    expect(tape).toHaveLength(TAPE.length - 2);
  });

  it("shows no change, rather than a made-up one, without a previous value", () => {
    const [nifty] = buildTape([latest("yf.^NSEI", 25104.3, null)], SERIES);
    expect(nifty).toMatchObject({ value: "25,104.30", change: null });
    const [us10] = buildTape([latest("fred.DGS10", 4.08, null)], SERIES);
    expect(us10.change).toBeNull();
  });

  it("is empty when nothing is published", () => {
    expect(buildTape([], SERIES)).toEqual([]);
    expect(buildTape(LATEST, [])).toEqual([]);
  });
});

describe("sourceLinesOf", () => {
  it("splits the source line from the copyright line", () => {
    expect(sourceLinesOf("Source: Nasdaq, Inc., via Yahoo Finance · © Nasdaq, Inc.")).toEqual([
      "Source: Nasdaq, Inc., via Yahoo Finance",
      "© Nasdaq, Inc.",
    ]);
    expect(sourceLinesOf(null)).toEqual([]);
  });
});
