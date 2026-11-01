// The Valuation tab's reading of the stored runs (2A-20): which run a company shows, every input a run stored with its
// date and source, the grid's centre and the cell a choice names, and the IMF context. No valuation maths is tested
// here because none runs in the browser: the cell read is the stored row, whatever its value.
import { describe, expect, it } from "vitest";
import { IMF_CPI, IMF_GDP, assumptionRows, cellOf, gridCentre, gridOptions, imfContext, leadKind, leadOf, num, runOf, storedInput } from "@/lib/equity/valuation";
import { OPERATING_INPUTS, forecast, grid, lenderRun, run } from "@/tests/valuation-rows";

describe("reading stored decimals", () => {
  it("reads strings and numbers, and nothing else", () => {
    expect(num("0.0531")).toBe(0.0531);
    expect(num(2)).toBe(2);
    expect(num("")).toBeNull();
    expect(num("x")).toBeNull();
    expect(num(null)).toBeNull();
    expect(storedInput({ value: "1.5", unit: "beta", as_of: "2026-10-30", source: "s" })).toMatchObject({ value: 1.5, unit: "beta", asOf: "2026-10-30", source: "s", held: null });
    expect(storedInput({ value: null, unit: "money", as_of: null, source: "s", held: "Not filed." })?.held).toBe("Not filed.");
    expect(storedInput("nope")).toBeNull();
  });
});

describe("which run leads", () => {
  it("reads a lender through its implied return on equity and everyone else through the reverse DCF", () => {
    expect(leadKind("lender")).toBe("implied_roe");
    for (const f of ["operating", "insurer", "mixed", null]) expect(leadKind(f)).toBe("reverse_dcf");
    expect(runOf([run("dcf_default"), run("reverse_dcf")], "reverse_dcf")?.kind).toBe("reverse_dcf");
    expect(runOf([], "reverse_dcf")).toBeNull();
  });
  it("takes the implied figure, the delivered one, the stored gap and the years from the run", () => {
    expect(leadOf(run("reverse_dcf"))).toMatchObject({ kind: "reverse_dcf", status: "solved", implied: 0.1213, delivered: 0.0912, gap: 0.0301, years: 5, label: "model-implied" });
    expect(leadOf(lenderRun())).toMatchObject({ kind: "implied_roe", implied: 0.153, delivered: 0.141, gap: 0.012, years: 3 });
  });
  it("has no gap when the engine stored none", () => {
    expect(leadOf(run("reverse_dcf", { inputs: { ...OPERATING_INPUTS, gap: undefined } })).gap).toBeNull();
    expect(leadOf(run("reverse_dcf", { inputs: null })).gap).toBeNull();
  });
});

describe("assumptionRows", () => {
  const rows = assumptionRows(run("reverse_dcf"));
  const byId = (id: string) => rows.find((r) => r.id === id)!;
  it("lists every input the model uses, each with its value, as-of date and source", () => {
    expect(rows.map((r) => r.id)).toEqual(["price", "revenue", "margin", "tax-near", "tax-far", "s2c", "rf", "erp", "beta", "ke", "kd", "weights", "wacc", "gt", "shares"]);
    for (const r of rows) {
      expect(r.source, r.id).not.toBe("");
      expect(r.asOf, r.id).not.toBeNull();
    }
    expect(byId("price")).toMatchObject({ value: 3456.7, asOf: "2026-10-30", kind: "price" });
    expect(byId("revenue")).toMatchObject({ value: 2.5e12, asOf: "2026-06-30" });
    expect(byId("rf")).toMatchObject({ value: 0.0531, asOf: "2026-09-01" });
    expect(byId("erp")).toMatchObject({ value: 0.0721, asOf: "2026-10-01" });
  });
  it("says in words how the risk-free rate, the premium and the cost of debt were built, from the stored detail", () => {
    expect(byId("rf").note).toBe("10-year government yield 7.06% less the default spread 1.75% (Damodaran edition of 1 Jul 2026)");
    expect(byId("erp").note).toBe("Implied mature-market premium 4.30% plus India's country risk premium 2.91% (Damodaran edition of 1 Jul 2026)");
    expect(byId("kd").note).toBe("Bounded up to the gross 10-year yield");
    expect(byId("beta").note).toBe("Industry: Software (System and Application)");
    expect(byId("weights")).toMatchObject({ value: 0.98, value2: 0.02 });
  });
  it("marks what the model derived, and not what the filings or the market gave", () => {
    expect(byId("margin").derived).toBe(true);
    expect(byId("wacc").derived).toBe(true);
    expect(byId("price").derived).toBe(false);
    expect(byId("beta").derived).toBe(false);
  });
  it("carries a held input's reason instead of a value", () => {
    const held = run("reverse_dcf", { inputs: { ...OPERATING_INPUTS, company: { ...OPERATING_INPUTS.company, shares: { value: null, unit: "shares", as_of: null, source: "s", held: "Shares outstanding today aren't stored." } } } });
    expect(assumptionRows(held).find((r) => r.id === "shares")).toMatchObject({ value: null, held: "Shares outstanding today aren't stored." });
  });
  it("reads a lender's inputs", () => {
    expect(assumptionRows(lenderRun()).map((r) => r.id)).toEqual(["price", "pb", "rf", "erp", "beta", "ke", "g", "delivered"]);
  });
  it("has no rows when the inputs are hidden", () => {
    expect(assumptionRows(run("reverse_dcf", { inputs: null }))).toEqual([]);
  });
});

describe("the scenario grid", () => {
  const rows = grid();
  it("offers the distinct values the grid holds", () => {
    expect(gridOptions(rows)).toEqual({ growth: [0.05, 0.075, 0.1], margin: [0.2112, 0.2412, 0.2712], wacc: [0.0921, 0.1021, 0.1121] });
  });
  it("starts at the 3-year margin, the model's WACC and the growth step nearest the default run's", () => {
    const def = run("dcf_default", { inputs: { growth: { used: "0.0812" }, drivers: { margin: "0.2412", wacc: "0.1021" } } });
    expect(gridCentre(gridOptions(rows), def)).toEqual({ growth: 0.075, margin: 0.2412, wacc: 0.1021 });
  });
  it("starts at the middle value when there is no default run, and nowhere when the grid is empty", () => {
    expect(gridCentre(gridOptions(rows), null)).toEqual({ growth: 0.075, margin: 0.2412, wacc: 0.1021 });
    expect(gridCentre(gridOptions([]), null)).toBeNull();
  });
  it("reads the stored cell a choice names, not a computed one, and null for a skipped combination", () => {
    expect(cellOf(rows, { growth: 0.075, margin: 0.2412, wacc: 0.1021 })?.value_per_share).toBe(1111);
    expect(cellOf(rows, { growth: 0.1, margin: 0.2112, wacc: 0.0921 })?.value_per_share).toBe(1200);
    expect(cellOf(rows, { growth: 0.2, margin: 0.2112, wacc: 0.0921 })).toBeNull();
  });
});

describe("imfContext", () => {
  const rows = [
    forecast(IMF_GDP, "2026-04-14", "2027", 6.1), forecast(IMF_GDP, "2026-10-13", "2026", 6.3), forecast(IMF_GDP, "2026-10-13", "2027", 6.5),
    forecast(IMF_GDP, "2026-10-13", "2028", 6.4), forecast(IMF_CPI, "2026-10-13", "2027", 4.1), forecast(IMF_CPI, "2026-10-13", "2028", null),
    ...["2029", "2030", "2031", "2032"].map((y) => forecast(IMF_GDP, "2026-10-13", y, 6)),
  ];
  it("shows the newest edition's next five years side by side, as published", () => {
    const ctx = imfContext(rows, "2026-10-31")!;
    expect(ctx.vintage).toBe("2026-10-13");
    expect(ctx.years.map((y) => y.year)).toEqual(["2026", "2027", "2028", "2029", "2030"]);
    expect(ctx.years[1]).toEqual({ year: "2027", gdp: 6.5, cpi: 4.1 });
    expect(ctx.years[2]).toEqual({ year: "2028", gdp: 6.4, cpi: null });
  });
  it("has none without forecasts", () => {
    expect(imfContext([], "2026-10-31")).toBeNull();
  });
});
