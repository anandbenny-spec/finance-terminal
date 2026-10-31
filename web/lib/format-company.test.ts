// The company-figure formats (module 2; Spec 02A §8): fractions as per cent, multiples, prices and crore, by the figure's market.
import { describe, expect, it } from "vitest";
import {
  formatFraction,
  formatFractionChange,
  formatFractionPoints,
  formatMultiple,
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
});
