// The screener's filters and cell states (2A-18). Test rows, not real figures.
import { describe, expect, it } from "vitest";
import { applyFilters, cellState, columnLabel, isActive, NO_FILTERS, parseBound, peerGroups, type Filters } from "@/lib/equity/screener";
import { row } from "@/tests/screener-rows";

const rows = [
  row("a"),
  row("b", { pe_ttm: 35, roe: 0.07, peer_group_key: "bank", peer_group_label: "Banks", market_cap: 1e12 }),
  row("c", { pe_ttm: null, notes: { pe_ttm: { label: null, reason: "Profit is not positive." } }, index_slugs: ["nifty-50", "nifty-next-50"] }),
  row("d", { debt_to_equity: null, notes: { debt_to_equity: { label: "borrowings only", reason: "N/A: not meaningful for a lender" } } }),
];
const slugs = (f: Filters) => applyFilters(rows, f).map((r) => r.slug);
const range = (key: keyof Filters["ranges"], min: string, max = ""): Filters => ({ ...NO_FILTERS, ranges: { [key]: { min, max } } });

describe("applyFilters", () => {
  it("returns every row with no filter, and says no filter is active", () => {
    expect(slugs(NO_FILTERS)).toEqual(["a", "b", "c", "d"]);
    expect(isActive(NO_FILTERS)).toBe(false);
  });
  it("narrows by a range and leaves out a row with no value for the filtered column", () => {
    expect(slugs(range("pe", "10", "30"))).toEqual(["a", "d"]);
    expect(slugs(range("pe", "30"))).toEqual(["b"]);
    expect(slugs(range("debtToEquity", "", "1"))).toEqual(["a", "b", "c"]);
  });
  it("compares a stored fraction with the per-cent the box is labelled in, without float noise", () => {
    expect(slugs(range("roe", "", "7"))).toEqual(["b"]);
    expect(slugs(range("roe", "18", "18"))).toEqual(["a", "c", "d"]);
  });
  it("compares market cap in crore against full rupees", () => {
    expect(slugs(range("marketCap", "200000"))).toEqual(["a", "c", "d"]); // ₹5 lakh crore vs ₹1 lakh crore
    expect(slugs(range("marketCap", "", "100000"))).toEqual(["b"]);
  });
  it("filters by index (any chosen) and by peer group, and ignores a box that isn't a number", () => {
    expect(slugs({ ...NO_FILTERS, indexes: ["nifty-next-50"] })).toEqual(["c"]);
    expect(slugs({ ...NO_FILTERS, indexes: ["nifty-next-50", "nifty-50"] })).toEqual(["a", "b", "c", "d"]);
    expect(slugs({ ...NO_FILTERS, peerGroup: "bank" })).toEqual(["b"]);
    expect(slugs(range("pe", "abc"))).toEqual(["a", "b", "c", "d"]);
    expect(isActive(range("pe", "abc"))).toBe(false);
    expect(isActive(range("pe", "1"))).toBe(true);
  });
  it("parses a bound with grouping commas", () => {
    expect(parseBound("1,25,000")).toBe(125000);
    expect(parseBound(" ")).toBeNull();
    expect(parseBound(undefined)).toBeNull();
  });
});

describe("cell states", () => {
  it("is a value when there is one, whatever the note says", () => {
    expect(cellState(rows[0], "pe_ttm", 20)).toEqual({ kind: "value" });
  });
  it("reads a stored reason as Held, and one starting N/A as not applicable", () => {
    expect(cellState(rows[2], "pe_ttm", null)).toEqual({ kind: "held", reason: "Profit is not positive." });
    expect(cellState(rows[3], "debt_to_equity", null)).toEqual({ kind: "na", reason: "N/A: not meaningful for a lender" });
  });
  it("is plainly missing with no reason", () => {
    expect(cellState(rows[0], "pe_ttm", null)).toEqual({ kind: "missing" });
  });
  it("takes a column's label from the first row that carries one", () => {
    expect(columnLabel(rows, "debt_to_equity")).toBe("borrowings only");
    expect(columnLabel(rows, "pb")).toBeNull();
  });
  it("lists peer groups by label, once each", () => {
    expect(peerGroups(rows)).toEqual([{ key: "bank", label: "Banks" }, { key: "it", label: "IT services" }]);
  });
});
