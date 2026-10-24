// web/lib/policy/rates.ts: the "Policy rates since 2019" chart's data (Spec 01 §4.2; Design 01 §2.3). Pure.
//
// The database returns step series as the days their value changed (api.observation_steps: N2) and the RBI's
// rates as the settings each MPC meeting made (api.policy_rate_settings). Lightweight Charts spaces points by index,
// not by date, so the chart needs one point per calendar day to keep time even: this module expands each step into
// the value in force on every day, from the range's first day to the last day the data runs to. Every value drawn
// is one the database returned; nothing is interpolated.
//
// - Fed target range: FRED DFEDTARU/L, from their own effective dates (D14), up to their latest observation.
// - RBI repo and corridor: as set at MPC meetings (D5), in force from each decision date. The corridor's floor is
//   the reverse repo before 8 Apr 2022 and the SDF from then (Spec 01 §2); its ceiling is the MSF.
import type { ObservationStepRow, PolicyMeetingRow, PolicyRateSettingRow } from "@/lib/queries/api";
import type { StepSeries } from "@/lib/queries/policy";

export type BandPoint = { time: string; low: number; high: number };
export type LinePoint = { time: string; value: number };

/** One day's values, for the readout and "View as table" (null where a series has no value that day). */
export type RateDay = {
  date: string;
  fedLower: number | null;
  fedUpper: number | null;
  repo: number | null;
  floor: number | null;
  floorKind: "reverse_repo" | "sdf" | null;
  msf: number | null;
};

export type DecisionMarker = { time: string; bank: "Fed" | "RBI"; price: number };

export type RateChartData = {
  days: RateDay[];
  fedBand: BandPoint[];
  rbiRepo: LinePoint[];
  rbiCorridor: BandPoint[];
  markers: DecisionMarker[];
  /** The rows behind "View as table": the days any rate changed, plus the last day. */
  changes: RateDay[];
  /** The last day the Fed series and the RBI decisions run to (the panel's "Data to"). */
  fedDataTo: string | null;
  rbiDataTo: string | null;
};

function nextDay(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

/** Every calendar day from `from` to `to`, inclusive. */
export function calendarDays(from: string, to: string): string[] {
  const out: string[] = [];
  for (let d = from; d <= to; d = nextDay(d)) out.push(d);
  return out;
}

/** A step series' value on each day: the last step on or before it; null before the first and after the latest. */
function inForce(steps: readonly ObservationStepRow[], days: readonly string[]): (number | null)[] {
  const sorted = [...steps].sort((a, b) => a.obs_date.localeCompare(b.obs_date));
  const latest = sorted.find((s) => s.is_latest)?.obs_date ?? sorted.at(-1)?.obs_date ?? null;
  let i = -1;
  return days.map((day) => {
    while (i + 1 < sorted.length && sorted[i + 1].obs_date <= day) i++;
    if (i < 0 || latest === null || day > latest) return null;
    return sorted[i].value;
  });
}

type RbiSetting = { repo: number | null; floor: number | null; floorKind: RateDay["floorKind"]; msf: number | null };

/** The RBI settings by decision date, oldest first. */
function rbiByDecision(settings: readonly PolicyRateSettingRow[]): [string, RbiSetting][] {
  const byDate = new Map<string, RbiSetting>();
  for (const s of settings) {
    const row = byDate.get(s.decision_date) ?? { repo: null, floor: null, floorKind: null, msf: null };
    if (s.instrument === "repo") row.repo = s.value_pct;
    else if (s.instrument === "msf") row.msf = s.value_pct;
    else if (s.instrument === "sdf") [row.floor, row.floorKind] = [s.value_pct, "sdf"];
    else if (s.instrument === "reverse_repo" && row.floorKind !== "sdf") [row.floor, row.floorKind] = [s.value_pct, "reverse_repo"];
    byDate.set(s.decision_date, row);
  }
  return [...byDate.entries()].sort(([a], [b]) => a.localeCompare(b));
}

/**
 * The chart's series from `from` on: the Fed band, the RBI repo step and the RBI corridor band, one point per day,
 * plus the decision markers (held rate decisions, at the rate they set) and the table's change rows.
 */
export function buildRateChart(
  fed: { upper: StepSeries; lower: StepSeries },
  rbi: readonly PolicyRateSettingRow[],
  decisions: readonly PolicyMeetingRow[],
  from: string,
): RateChartData {
  const fedDataTo = [...fed.upper.steps, ...fed.lower.steps].reduce<string | null>(
    (max, s) => (max === null || s.obs_date > max ? s.obs_date : max),
    null,
  );
  const rbiSettings = rbiByDecision(rbi);
  const rbiDataTo = rbiSettings.at(-1)?.[0] ?? null;
  const end = [fedDataTo, rbiDataTo].filter((d): d is string => d !== null).sort().at(-1);
  if (!end || end < from) {
    return { days: [], fedBand: [], rbiRepo: [], rbiCorridor: [], markers: [], changes: [], fedDataTo, rbiDataTo };
  }

  const dates = calendarDays(from, end);
  const upper = inForce(fed.upper.steps, dates);
  const lower = inForce(fed.lower.steps, dates);
  let j = -1;
  const days: RateDay[] = dates.map((date, i) => {
    while (j + 1 < rbiSettings.length && rbiSettings[j + 1][0] <= date) j++;
    const r = j >= 0 ? rbiSettings[j][1] : null;
    const bothFed = upper[i] !== null && lower[i] !== null;
    return {
      date,
      fedLower: bothFed ? lower[i] : null,
      fedUpper: bothFed ? upper[i] : null,
      repo: r?.repo ?? null,
      floor: r?.floor ?? null,
      floorKind: r?.floorKind ?? null,
      msf: r?.msf ?? null,
    };
  });

  const fedBand: BandPoint[] = [];
  const rbiRepo: LinePoint[] = [];
  const rbiCorridor: BandPoint[] = [];
  for (const d of days) {
    if (d.fedLower !== null && d.fedUpper !== null) fedBand.push({ time: d.date, low: d.fedLower, high: d.fedUpper });
    if (d.repo !== null) rbiRepo.push({ time: d.date, value: d.repo });
    if (d.floor !== null && d.msf !== null) rbiCorridor.push({ time: d.date, low: d.floor, high: d.msf });
  }

  const markers: DecisionMarker[] = [];
  for (const m of decisions) {
    if (m.decision_date < from || m.decision_date > end) continue;
    const price = m.bank === "Fed" ? m.fed_target_upper_pct : m.policy_rate_pct;
    if (price === null || (m.bank !== "Fed" && m.bank !== "RBI")) continue;
    markers.push({ time: m.decision_date, bank: m.bank, price });
  }
  markers.sort((a, b) => a.time.localeCompare(b.time) || a.bank.localeCompare(b.bank));

  const same = (a: RateDay, b: RateDay) =>
    a.fedLower === b.fedLower && a.fedUpper === b.fedUpper && a.repo === b.repo && a.floor === b.floor && a.msf === b.msf;
  const changes = days.filter((d, i) => i === 0 || i === days.length - 1 || !same(d, days[i - 1]));

  return { days, fedBand, rbiRepo, rbiCorridor, markers, changes, fedDataTo, rbiDataTo };
}

/** The same calendar date `years` earlier (29 Feb falls back to 28 Feb): the range presets' first day. */
export function yearsBefore(date: string, years: number): string {
  const [y, m, d] = date.split("-").map(Number);
  const target = new Date(Date.UTC(y - years, m - 1, d));
  if (target.getUTCMonth() !== m - 1) target.setUTCDate(0); // 29 Feb in a year without one
  return target.toISOString().slice(0, 10);
}

/** The day's values for the readout: the hovered day, else the last day. */
export function dayAt(days: readonly RateDay[], date: string | null): RateDay | null {
  if (days.length === 0) return null;
  if (date === null) return days[days.length - 1];
  // Days are consecutive calendar days from days[0], so the index is the day count.
  const i = Math.round((Date.parse(`${date}T00:00:00Z`) - Date.parse(`${days[0].date}T00:00:00Z`)) / 86_400_000);
  return days[i]?.date === date ? days[i] : null;
}
