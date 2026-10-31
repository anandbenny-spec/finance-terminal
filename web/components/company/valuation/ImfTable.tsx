"use client";

// The IMF context (2A-20; decided by Claude, standing delegation, on the card): the IMF's forecasts of India's real GDP
// growth and CPI inflation for the next five years, side by side, as published, with no arithmetic on them. The edition
// is named in the panel's subtitle by its stored release date.
import { useMemo } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { StateNote } from "@/components/data/StateNote";
import type { ImfYear } from "@/lib/equity/valuation";
import { NOT_AVAILABLE, formatAxisNumber } from "@/lib/format";

export function ImfTable({ years }: { years: ImfYear[] }) {
  const columns = useMemo<Column<ImfYear>[]>(
    () => [
      { id: "year", header: "Year", cell: (r) => <span className="font-mono">{r.year}</span> },
      { id: "gdp", header: "Real GDP growth, India (%)", align: "right", cell: (r) => (r.gdp === null ? NOT_AVAILABLE : formatAxisNumber(r.gdp, 1)) },
      { id: "cpi", header: "CPI inflation, India (%)", align: "right", cell: (r) => (r.cpi === null ? NOT_AVAILABLE : formatAxisNumber(r.cpi, 1)) },
    ],
    [],
  );
  return (
    <DataTable
      columns={columns}
      rows={years}
      rowKey={(r) => r.year}
      caption="The IMF's forecasts of India's real GDP growth and CPI inflation for each of the next five years"
      dense
      empty={<StateNote kind="empty">No forecasts for the coming years are stored.</StateNote>}
    />
  );
}
