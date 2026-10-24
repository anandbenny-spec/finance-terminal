// web/lib/policy/numbers.ts: what NumbersChanged shows (Design 01 §1.16 layer 2, §4.5; Spec 01 §4.3 "What changed").
// Pure: this meeting's rows and the previous meeting's in, the table's items out, every number through lib/format.ts.
//
// - RBI: the rates it set (repo, reverse repo or SDF, MSF, bank rate, CRR), the stance (AI-extracted, only where its
//   field policy shows it) and each CPI and GDP projection. Fed: the target range and the vote.
// - Rates and projections are compared only where both meetings have the item; projections only for the same target
//   period (the same `target_label` in the same series) across the two vintages. Anything without a counterpart on
//   the other side is left out, never guessed. Changes carry a sign but no colour (a revision isn't a market move).
// - Each item says whether it changed; the component folds the unchanged ones into one line.
import { NOT_AVAILABLE, formatAxisChange, formatBpsChange, formatProjection, formatRate, formatRateRange } from "@/lib/format";
import { stanceText, tally } from "@/lib/policy/decisions";
import type { ForecastRow, PolicyMeetingRow, PolicyRateSettingRow, PolicyVoteRow } from "@/lib/queries/api";

export type NumberItem = {
  key: string;
  label: string;
  previous: string;
  next: string;
  /** The signed change, or null where the item has no number to subtract (the stance, the vote). */
  change: string | null;
  changed: boolean;
  /** "AI-extracted", for the stance. */
  qualifier: string | null;
};

export type NumbersInput = {
  current: PolicyMeetingRow;
  previous: PolicyMeetingRow;
  currentSettings: readonly PolicyRateSettingRow[];
  previousSettings: readonly PolicyRateSettingRow[];
  currentProjections: readonly ForecastRow[];
  previousProjections: readonly ForecastRow[];
  currentVotes: readonly PolicyVoteRow[];
  previousVotes: readonly PolicyVoteRow[];
  /** The stance's field policy: show it (and with which label), or hold it back. */
  stance: { show: boolean; label: string | null };
};

const RBI_RATES: { instrument: string; label: string }[] = [
  { instrument: "repo", label: "Policy repo rate" },
  { instrument: "reverse_repo", label: "Reverse repo rate" },
  { instrument: "sdf", label: "Standing deposit facility (SDF)" },
  { instrument: "msf", label: "Marginal standing facility (MSF)" },
  { instrument: "bank_rate", label: "Bank rate" },
  { instrument: "crr", label: "Cash reserve ratio (CRR)" },
];

const VARIABLE: Record<string, string> = { "rbi.proj_cpi": "CPI inflation", "rbi.proj_gdp": "Real GDP growth" };

/** A difference in hundredths of a point, as whole bps (rounded: 5.25 − 5.0 is exact enough only after rounding). */
function bpsBetween(from: number, to: number): number {
  return Math.round((to - from) * 100);
}

function rateOf(rows: readonly PolicyRateSettingRow[], slug: string, instrument: string): number | null {
  return rows.find((r) => r.meeting_slug === slug && r.instrument === instrument)?.value_pct ?? null;
}

function fedItems(i: NumbersInput): NumberItem[] {
  const { current: cur, previous: prev } = i;
  const items: NumberItem[] = [];
  const lo = [rateOf(i.previousSettings, prev.slug, "fed_target_lower"), rateOf(i.currentSettings, cur.slug, "fed_target_lower")] as const;
  const up = [rateOf(i.previousSettings, prev.slug, "fed_target_upper"), rateOf(i.currentSettings, cur.slug, "fed_target_upper")] as const;
  if (lo[0] !== null && lo[1] !== null && up[0] !== null && up[1] !== null) {
    const dLo = bpsBetween(lo[0], lo[1]);
    const dUp = bpsBetween(up[0], up[1]);
    items.push({
      key: "fed_target",
      label: "Target range",
      previous: formatRateRange(lo[0], up[0]),
      next: formatRateRange(lo[1], up[1]),
      change: dLo === dUp ? formatBpsChange(dUp) : NOT_AVAILABLE,
      changed: dLo !== 0 || dUp !== 0,
      qualifier: null,
    });
  }
  const before = tally(prev, i.previousVotes);
  const now = tally(cur, i.currentVotes);
  if (before.kind === "tally" && now.kind === "tally") {
    items.push({ key: "vote", label: "Vote", previous: before.short, next: now.short, change: null, changed: before.short !== now.short, qualifier: null });
  }
  return items;
}

function rbiRates(i: NumbersInput): NumberItem[] {
  const items: NumberItem[] = [];
  for (const { instrument, label } of RBI_RATES) {
    const before = rateOf(i.previousSettings, i.previous.slug, instrument);
    const now = rateOf(i.currentSettings, i.current.slug, instrument);
    if (before === null || now === null) continue;
    const d = bpsBetween(before, now);
    items.push({ key: instrument, label, previous: formatRate(before), next: formatRate(now), change: formatBpsChange(d), changed: d !== 0, qualifier: null });
  }
  return items;
}

function rbiStance(i: NumbersInput): NumberItem[] {
  const { stance, current, previous } = i;
  if (!stance.show || current.stance === null || previous.stance === null) return [];
  return [
    {
      key: "stance",
      label: "Stance",
      previous: stanceText(previous.stance),
      next: stanceText(current.stance),
      change: null,
      changed: current.stance !== previous.stance,
      qualifier: stance.label,
    },
  ];
}

function shape(f: ForecastRow): string {
  return f.value_low !== null ? formatProjection(f.value_low, f.value_high) : formatProjection(f.value);
}

function same(a: number | null, b: number | null): boolean {
  return a === b;
}

function rbiProjections(i: NumbersInput): NumberItem[] {
  const items: NumberItem[] = [];
  for (const f of i.currentProjections) {
    const g = i.previousProjections.find((p) => p.series_key === f.series_key && p.target_label === f.target_label);
    if (!g) continue; // the same target period isn't in the earlier vintage: nothing to compare
    const hasNow = f.value !== null || f.value_low !== null;
    const hadBefore = g.value !== null || g.value_low !== null;
    if (!hasNow || !hadBefore) continue;
    const changed = !same(f.value, g.value) || !same(f.value_low, g.value_low) || !same(f.value_high, g.value_high);
    const point = f.value_low === null && g.value_low === null && f.value !== null && g.value !== null;
    items.push({
      key: `${f.series_key}-${f.target_label}`,
      label: `${VARIABLE[f.series_key] ?? f.series_name}, ${f.target_label}`,
      previous: shape(g),
      next: shape(f),
      change: point ? `${formatAxisChange(Math.round((f.value! - g.value!) * 10) / 10, 1)} pp` : NOT_AVAILABLE,
      changed,
      qualifier: null,
    });
  }
  return items;
}

/** NumbersChanged's items for a meeting against the one before it, in the table's order. */
export function numbersChanged(i: NumbersInput): NumberItem[] {
  return i.current.bank === "Fed" ? fedItems(i) : [...rbiRates(i), ...rbiStance(i), ...rbiProjections(i)];
}
