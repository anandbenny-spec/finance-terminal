"use client";

// "Set your own scenario" (2A-20; Spec 02A §8, §5.7, §10 decision 7): three selects (growth, EBIT margin, WACC) over the
// grid the weekly run stored. The value shown is the stored cell the three choices name, labelled "Model-implied value
// under selected assumptions": no valuation maths runs here, no comparison with the price (which this panel never
// shows), no upside, and no colour on the value (Brief §11). A combination the grid skipped, or a cell with no value,
// says so with its stored reason.
import { useMemo, useState, type ReactNode } from "react";
import { Qualifier } from "@/components/data/Qualifier";
import { Stat } from "@/components/data/Stat";
import { StateNote } from "@/components/data/StateNote";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cellOf, type GridChoice, type GridOptions } from "@/lib/equity/valuation";
import { formatDate, formatFraction, formatPrice, type Currency } from "@/lib/format";
import type { ValuationScenarioRow } from "@/lib/queries/api";

export const SCENARIO_LABEL = "Model-implied value under selected assumptions";

function Field({ id, label, value, options, onChange }: { id: string; label: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void }) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="mb-1 block text-meta text-ink-2">
        {label}
      </label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function ScenarioControls({
  rows,
  options,
  initial,
  currency,
}: {
  rows: ValuationScenarioRow[];
  options: GridOptions;
  initial: GridChoice;
  currency: Currency;
}) {
  const [growth, setGrowth] = useState(String(initial.growth));
  const [margin, setMargin] = useState(String(initial.margin));
  const [wacc, setWacc] = useState(String(initial.wacc));
  const list = (values: number[], centre: number, decimals: number, tag: string) =>
    values.map((v) => ({ value: String(v), label: `${formatFraction(v, decimals)}${v === centre ? ` (${tag})` : ""}` }));
  const growthOptions = useMemo(() => list(options.growth, initial.growth, 1, "default"), [options.growth, initial.growth]);
  const marginOptions = useMemo(() => list(options.margin, initial.margin, 1, "3-year level"), [options.margin, initial.margin]);
  const waccOptions = useMemo(() => list(options.wacc, initial.wacc, 2, "model's own"), [options.wacc, initial.wacc]);
  const cell = cellOf(rows, { growth: Number(growth), margin: Number(margin), wacc: Number(wacc) });

  let context: ReactNode;
  let value = "—";
  if (!cell) {
    context = <StateNote kind="empty">The stored grid has no value for this combination.</StateNote>;
  } else if (cell.value_per_share === null) {
    context = <StateNote kind={cell.state === "held" ? "held" : "unavailable"}>{cell.reason ?? "No value exists at these assumptions."}</StateNote>;
  } else {
    value = formatPrice(cell.value_per_share, currency);
    context = cell.label ? <Qualifier>{cell.label}</Qualifier> : undefined;
  }

  return (
    <div className="grid gap-4 p-3 md:grid-cols-3">
      <Field id="scenario-growth" label="Revenue growth a year, years 1 to 5" value={growth} options={growthOptions} onChange={setGrowth} />
      <Field id="scenario-margin" label="EBIT margin" value={margin} options={marginOptions} onChange={setMargin} />
      <Field id="scenario-wacc" label="Cost of capital (WACC)" value={wacc} options={waccOptions} onChange={setWacc} />
      <div className="md:col-span-3" aria-live="polite">
        <Stat label={SCENARIO_LABEL} value={value} unit={cell && cell.value_per_share !== null ? "per share" : undefined} context={context} asOf={cell ? `Grid run on ${formatDate(cell.as_of)}` : undefined} />
      </div>
    </div>
  );
}
