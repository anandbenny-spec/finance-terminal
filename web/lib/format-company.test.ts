// The company-figure formats (module 2; Spec 02A §8): fractions as per cent, multiples, prices and crore, by the figure's market.
import { describe, expect, it } from "vitest";
import {
  formatAmount,
  formatCount,
  formatDollarsAsMillion,
  formatFraction,
  formatFractionChange,
  formatFractionPoints,
  formatMoney,
  formatMultiple,
  formatPercentLevel,
  formatPrice,
  formatRupeesAsCrore,
  NOT_AVAILABLE,
} from "@/lib/format";

describe("company figures", () => {
  it("shows a stored fraction as a percentage with a true minus", () => {
    expect(formatFraction(0.1834)).toBe("18.3%");
    expect(formatFraction(-0.05)).toBe("−5.0%");
    expect(formatFraction(0.07)).toBe("7.0%");
    expect(formatFraction(null)).toBe(NOT_AVAILABLE);
  });
  it("signs a market move and a gap, and leaves a zero unsigned", () => {
    expect(formatFractionChange(0.2)).toBe("+20.0%");
    expect(formatFractionChange(-0.0412)).toBe("−4.1%");
    expect(formatFractionChange(0)).toBe("0.0%");
    expect(formatFractionPoints(0.04)).toBe("+4.0 pts");
    expect(formatFractionPoints(-0.035)).toBe("−3.5 pts");
  });
  it("groups multiples by the figure's market, never the viewer's locale", () => {
    expect(formatMultiple(23.456)).toBe("23.5×");
    expect(formatMultiple(1234567.8, "IN")).toBe("12,34,567.8×");
    expect(formatMultiple(1234567.8, "US")).toBe("1,234,567.8×");
    expect(formatMultiple(null)).toBe(NOT_AVAILABLE);
  });
  it("shows a price in its own currency", () => {
    expect(formatPrice(1234.5, "INR")).toBe("₹1,234.50");
    expect(formatPrice(98.1, "USD")).toBe("$98.10");
  });
  it("shows full rupees as crore with Indian grouping", () => {
    expect(formatRupeesAsCrore(1_234_500_000_000)).toBe("₹1,23,450 cr");
    expect(formatRupeesAsCrore(null)).toBe(NOT_AVAILABLE);
  });
  // US-06 (#405): the US twin of crore, so a Dow 30 figure reads like a 10-K's ($ millions), grouped the US way.
  it("shows full dollars as millions with US grouping, a true minus, and no sign on a rounded zero", () => {
    expect(formatDollarsAsMillion(394_328_000_000)).toBe("$394,328 mn");
    expect(formatDollarsAsMillion(3_912_400_000_000)).toBe("$3,912,400 mn");
    expect(formatDollarsAsMillion(-1_234_600_000)).toBe("−$1,235 mn");
    expect(formatDollarsAsMillion(-400_000)).toBe("$0 mn");
    expect(formatDollarsAsMillion(null)).toBe(NOT_AVAILABLE);
  });
  it("puts each amount in its own currency's unit, never the other's", () => {
    expect(formatAmount(1_234_500_000_000, "INR")).toBe("₹1,23,450 cr");
    expect(formatAmount(1_234_500_000_000, "USD")).toBe("$1,234,500 mn");
    expect(formatAmount(null, "USD")).toBe(NOT_AVAILABLE);
    expect(() => formatMoney(100, "INR", { unit: "million" })).toThrow(RangeError);
    expect(() => formatMoney(100, "USD", { unit: "crore" })).toThrow(RangeError);
  });
  it("groups a company's own count by its market", () => {
    expect(formatCount(3_600_000_000, "IN")).toBe("3,60,00,00,000");
    expect(formatCount(3_600_000_000, "US")).toBe("3,600,000,000");
    expect(formatCount(1234)).toBe("1,234");
  });
  it("shows a percentage level that may exceed 100 grouped by its market (the promoter note)", () => {
    expect(formatPercentLevel(6613)).toBe("6,613.00%");
    expect(formatPercentLevel(66.13)).toBe("66.13%");
    expect(formatPercentLevel(1234567, "US", 0)).toBe("1,234,567%");
    expect(formatPercentLevel(null)).toBe(NOT_AVAILABLE);
  });
});
