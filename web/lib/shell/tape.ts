// web/lib/shell/tape.ts: the ticker tape's items (Spec 01 §4.1; Design 01 §1.7, §4.1). Pure: rows in, display
// strings out, every figure through lib/format.ts. TickerTape renders what this returns.
//
// - Order and labels are this config list; values come from api.series_latest (the last and previous observation:
//   F24), source lines from api.series. An item whose series isn't published (RLS returns no row) is left out.
// - Indices and USD/INR show their change in %, the US 10-year in bps; the India 10-year is monthly, so it carries
//   the "monthly" qualifier and its month instead of a change.
// - USD/INR here is yf.INR=X, the latest day only; history and charts use fred.DEXINUS, never spliced (Spec 01 D6).
import type { SeriesLatestRow, SeriesRow } from "@/lib/queries/api";
import { changeOf, type Change } from "@/lib/delta";
import {
  formatDate,
  formatIndexLevel,
  formatMonth,
  formatPctChange,
  formatUsdInr,
  formatYield,
  formatYieldChange,
  type Market,
} from "@/lib/format";

type Kind = "index" | "fx" | "yield" | "monthly-yield";

type TapeConfigItem = { key: string; label: string; kind: Kind; market?: Market };

/** The tape, in order (Spec 01 §4.1). Bitcoin joins with module 4. */
export const TAPE: readonly TapeConfigItem[] = [
  { key: "yf.^NSEI", label: "Nifty 50", kind: "index", market: "IN" },
  { key: "yf.^BSESN", label: "Sensex", kind: "index", market: "IN" },
  { key: "yf.^GSPC", label: "S&P 500", kind: "index", market: "US" },
  { key: "yf.^IXIC", label: "Nasdaq", kind: "index", market: "US" },
  { key: "yf.^NDX", label: "Nasdaq-100", kind: "index", market: "US" },
  { key: "yf.^DJI", label: "Dow", kind: "index", market: "US" },
  { key: "yf.INR=X", label: "USD/INR", kind: "fx" },
  { key: "fred.DGS10", label: "US 10Y", kind: "yield" },
  { key: "fred.INDIRLTLT01STM", label: "IN 10Y", kind: "monthly-yield" },
];

export type TapeItem = {
  key: string;
  label: string; // on the tape
  name: string; // the series' full name, in the tooltip
  value: string;
  change: Change | null;
  month: string | null; // monthly series: the month the value is for, shown on the tape
  qualifier: "monthly" | null;
  date: string; // the observation's date (a monthly series': its month), in the tooltip
  sourceLines: string[]; // the source line, then the copyright line where the series has one
  description: string; // everything above in one sentence, for screen readers
};

/** A series' attribution split into its lines: "Source: …, via Yahoo Finance · © …" → [source, copyright]. */
export function sourceLinesOf(attribution: string | null): string[] {
  if (!attribution) return [];
  return attribution
    .split(" · ")
    .map((s) => s.trim())
    .filter(Boolean);
}

function pctChange(last: number, prev: number | null): string | null {
  if (prev === null || !(prev > 0)) return null;
  return formatPctChange((last / prev - 1) * 100);
}

function display(item: TapeConfigItem, row: SeriesLatestRow): Pick<TapeItem, "value" | "change" | "month" | "qualifier"> {
  switch (item.kind) {
    case "index": {
      const change = pctChange(row.last_value, row.prev_value);
      return { value: formatIndexLevel(row.last_value, item.market!), change: change ? changeOf(change) : null, month: null, qualifier: null };
    }
    case "fx": {
      const change = pctChange(row.last_value, row.prev_value);
      return { value: formatUsdInr(row.last_value), change: change ? changeOf(change) : null, month: null, qualifier: null };
    }
    case "yield": {
      const pp = row.prev_value === null ? null : row.last_value - row.prev_value;
      return { value: formatYield(row.last_value), change: pp === null ? null : changeOf(formatYieldChange(pp)), month: null, qualifier: null };
    }
    case "monthly-yield":
      return { value: formatYield(row.last_value), change: null, month: formatMonth(row.last_date), qualifier: "monthly" };
  }
}

/** The tape's items from the two views' rows: config order, published series only. */
export function buildTape(latest: readonly SeriesLatestRow[], series: readonly SeriesRow[]): TapeItem[] {
  const latestByKey = new Map(latest.map((r) => [r.series_key, r]));
  const seriesByKey = new Map(series.map((s) => [s.key, s]));
  const items: TapeItem[] = [];
  for (const item of TAPE) {
    const row = latestByKey.get(item.key);
    const meta = seriesByKey.get(item.key);
    if (!row || !meta) continue; // not published (or no observation yet): left out, never a dash
    const shown = display(item, row);
    const date = item.kind === "monthly-yield" ? formatMonth(row.last_date) : formatDate(row.last_date);
    const sourceLines = sourceLinesOf(meta.attribution);
    const figure = [shown.value, shown.change?.spoken, shown.qualifier ? `${shown.qualifier}, ${date}` : date]
      .filter(Boolean)
      .join(", ");
    const description = [`${item.label} (${meta.name}): ${figure}.`, ...sourceLines.map((l) => `${l}.`)].join(" ");
    items.push({ key: item.key, label: item.label, name: meta.name, ...shown, date, sourceLines, description });
  }
  return items;
}
