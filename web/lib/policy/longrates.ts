// web/lib/policy/longrates.ts: the "Long rates: US vs India 10-year" panel's data (Spec 01 §4.2; Pass 1b, #71). Pure.
//
// Two series, both read whole by `readDailySeries` (N2): the US 10-year Treasury yield (FRED DGS10, daily, cyan)
// and India's 10-year government bond yield (FRED INDIRLTLT01STM, OECD, monthly, violet: it is never drawn as
// daily). Every point drawn is a row the database returned; nothing is interpolated or filled. The two are in
// different markets but both are yields in percent, so they share one axis without converting anything.
import type { ObservationRow } from "@/lib/queries/api";

export const US_10Y = "fred.DGS10";
export const IN_10Y = "fred.INDIRLTLT01STM";

export type YieldPoint = { time: string; value: number };

/** One row of "View as table": a date either series has a value on (null where it has none). */
export type LongRatesRow = { date: string; us: number | null; india: number | null };

export type LongRatesData = {
  us: YieldPoint[];
  india: YieldPoint[];
  /** Newest first. */
  rows: LongRatesRow[];
  /** The latest observation date of each series (the panel's "Data to"). */
  usTo: string | null;
  indiaTo: string | null;
};

/** Sorted by date, one value per date (the later row wins), non-finite values dropped. */
function clean(rows: readonly ObservationRow[], from: string): YieldPoint[] {
  const byDate = new Map<string, number>();
  for (const r of rows) {
    if (r.obs_date >= from && Number.isFinite(r.value)) byDate.set(r.obs_date, r.value);
  }
  return [...byDate].sort(([a], [b]) => a.localeCompare(b)).map(([time, value]) => ({ time, value }));
}

export function buildLongRates(us: readonly ObservationRow[], india: readonly ObservationRow[], from: string): LongRatesData {
  const usPoints = clean(us, from);
  const indiaPoints = clean(india, from);
  const usBy = new Map(usPoints.map((p) => [p.time, p.value]));
  const indiaBy = new Map(indiaPoints.map((p) => [p.time, p.value]));
  const dates = [...new Set([...usBy.keys(), ...indiaBy.keys()])].sort((a, b) => b.localeCompare(a));
  return {
    us: usPoints,
    india: indiaPoints,
    rows: dates.map((date) => ({ date, us: usBy.get(date) ?? null, india: indiaBy.get(date) ?? null })),
    usTo: usPoints.at(-1)?.time ?? null,
    indiaTo: indiaPoints.at(-1)?.time ?? null,
  };
}

/** The last point on or before `date` (null before the first). Points are sorted by date. */
function onOrBefore(points: readonly YieldPoint[], date: string): YieldPoint | null {
  let lo = 0;
  let hi = points.length - 1;
  let found: YieldPoint | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].time <= date) {
      found = points[mid];
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

/**
 * The readout for a hovered day (or the latest day when none is hovered): the US value on exactly that day, and the
 * month India's latest reading belongs to with its value. The US value is null on a day with no US close.
 */
export function readoutAt(data: LongRatesData, date: string | null): { date: string; us: number | null; india: YieldPoint | null } | null {
  const day = date ?? data.usTo ?? data.indiaTo;
  if (day === null) return null;
  const us = data.us.find((p) => p.time === day)?.value ?? null;
  return { date: day, us, india: onOrBefore(data.india, day) };
}
