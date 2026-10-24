// web/lib/policy/meeting.ts: what a meeting page shows (Spec 01 §4.3, §4.5; Design 01 §5.3), as rows and copy.
// Pure: the views' rows and the day in, text out, every number through lib/format.ts.
//
// The §4.5 state copy is used word for word where Spec 01 gives it. Two cases differ from the spec, by Anand's
// decision of 23 Oct 2026 (#15): RBI press-conference transcripts exist from Dec 2018, not Feb 2023, except for the
// four 2020 COVID meetings (a video address only). Their copy was approved by Anand on 24 Oct 2026, 13:54 IST (#21),
// with the COVID meetings' line saying only what RBI's pages show: no transcript, a video address. It holds once
// policy.rbi_documents stores the transcripts (B6 part 3, #15), which is why C4 merges after that.
import {
  NOT_AVAILABLE,
  formatAxisChange,
  formatBpsChange,
  formatDate,
  formatProjection,
  formatRate,
  formatReleaseTime,
  type EventZone,
} from "@/lib/format";
import { stanceText } from "@/lib/policy/decisions";
import { paragraphAt, type Paragraph } from "@/lib/policy/text";
import type { DocumentRow, ForecastRow, PolicyMeetingRow, PolicyRateSettingDetailRow, PolicyVoteRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";

export const ZONE: Record<Bank, EventZone> = { Fed: "America/New_York", RBI: "Asia/Kolkata" };

/** The URL segment of each bank (/policy/fed/…, /policy/rbi/…) and its slug prefix. */
export const BANK_PATH = { fed: "Fed", rbi: "RBI" } as const satisfies Record<string, Bank>;
const PREFIX: Record<Bank, string> = { Fed: "fomc", RBI: "mpc" };

/** The meeting's slug from its route, or null for a malformed date (the page answers 404). */
export function slugFor(bank: Bank, date: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (d.toISOString().slice(0, 10) !== date) return null;
  return `${PREFIX[bank]}-${date}`;
}

/** The document types behind each tab. */
export const DOC_TYPES = {
  Fed: { decision: "fomc_statement", governor: null, minutes: "fomc_minutes", transcript: "fomc_presconf_transcript" },
  RBI: { decision: "mpc_resolution", governor: "rbi_governor_statement", minutes: "mpc_minutes", transcript: "rbi_presconf_transcript" },
} as const;

/** Each document type's latest version (a changed text is a new version; the old one stays stored). */
export function latestByType(docs: readonly DocumentRow[]): Map<string, DocumentRow> {
  const out = new Map<string, DocumentRow>();
  for (const d of docs) {
    const had = out.get(d.doc_type);
    if (!had || d.version_no > had.version_no) out.set(d.doc_type, d);
  }
  return out;
}

/** The page's title: "FOMC meeting · 17 Sep 2026", "MPC meeting no. 61 · 7 Oct 2026". */
export function meetingTitle(m: PolicyMeetingRow): string {
  const date = formatDate(m.decision_date);
  if (m.bank === "Fed") return `FOMC meeting · ${date}`;
  return m.meeting_no ? `MPC meeting no. ${m.meeting_no} · ${date}` : `MPC meeting · ${date}`;
}

/** "Statement 14:00 ET (23:30 IST)", "Resolution 10:00 IST"; null without a release time. */
export function releaseLine(m: PolicyMeetingRow): string | null {
  if (!m.decision_at) return null;
  const bank = m.bank as Bank;
  return `${bank === "Fed" ? "Statement" : "Resolution"} ${formatReleaseTime(m.decision_at, ZONE[bank])}`;
}

// ------------------------------------------------------------------ §4.5 state copy

/** Spec 01 §4.5, "Scheduled meeting". */
export function scheduledNote(m: PolicyMeetingRow): string {
  const date = formatDate(m.decision_date);
  return m.decision_at ? `Decision due ${date}, ${formatReleaseTime(m.decision_at, ZONE[m.bank as Bank])}.` : `Decision due ${date}.`;
}

/** Spec 01 §4.5, "Special meeting no. 39" (RBI Act, s.45ZN). */
export const SPECIAL_MEETING_NOTE =
  "Special meeting to prepare the report to the Government on missing the inflation target (RBI Act, s.45ZN). No policy decision; no minutes published.";

/** Spec 01 §4.5, "Minutes not out yet"; after the due day, that they're late. */
export function minutesNote(m: PolicyMeetingRow, today: string): string {
  if (!m.minutes_due_on) return "The minutes aren't on the site yet.";
  const due = formatDate(m.minutes_due_on);
  if (today > m.minutes_due_on) return `The minutes were due on ${due} and aren't on the site yet.`;
  return m.bank === "RBI"
    ? `Minutes are due on ${due}, 14 days after the meeting (RBI Act, s.45ZL).`
    : `Minutes are due on ${due}, three weeks after the decision.`;
}

/** The four 2020 MPC meetings with a video address and no press conference (Anand, 23 Oct 2026, #15). */
export const RBI_NO_PRESS_CONFERENCE = new Set(["2020-03-27", "2020-05-22", "2020-08-06", "2020-10-09"]);

function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

/** Why the Press conference tab has no transcript. */
export function transcriptNote(m: PolicyMeetingRow, today: string): string {
  const late = daysBetween(m.decision_date, today) > 30;
  if (m.bank === "Fed") {
    if (m.has_press_conference === false) return "The Fed held no press conference after this meeting.";
    // Spec 01 §4.5, "Fed transcript missing".
    return late ? "Not published by the Fed." : "The Fed hasn't posted this transcript yet.";
  }
  // Spec 01 §4.5's "from February 2023" no longer holds (Anand, 23 Oct 2026, #15); this copy approved 24 Oct (#21).
  if (RBI_NO_PRESS_CONFERENCE.has(m.decision_date)) return "The RBI published no press-conference transcript for this meeting; the Governor gave a video address.";
  return late ? "Not published by the RBI." : "The RBI hasn't posted this transcript yet.";
}

// ------------------------------------------------------------------ Tables

const INSTRUMENT: Record<string, { label: string; order: number }> = {
  fed_target_lower: { label: "Target range, lower bound", order: 1 },
  fed_target_upper: { label: "Target range, upper bound", order: 2 },
  repo: { label: "Policy repo rate", order: 3 },
  reverse_repo: { label: "Reverse repo rate", order: 4 },
  sdf: { label: "Standing deposit facility (SDF)", order: 4 },
  msf: { label: "Marginal standing facility (MSF)", order: 5 },
  bank_rate: { label: "Bank rate", order: 6 },
};

export type RateRow = { key: string; label: string; value: string; change: string; para: number | null; note: string | null };

/** The rates the meeting set, each with its ¶ in the decision document and any cross-check note (B6, #21). */
export function rateRows(settings: readonly PolicyRateSettingDetailRow[], paras: readonly Paragraph[], decisionDocId: number | null): RateRow[] {
  return settings
    .filter((s) => INSTRUMENT[s.instrument])
    .sort((a, b) => INSTRUMENT[a.instrument].order - INSTRUMENT[b.instrument].order)
    .map((s) => ({
      key: s.instrument,
      label: INSTRUMENT[s.instrument].label,
      value: formatRate(s.value_pct),
      change: formatBpsChange(s.change_bps),
      para: s.document_id === decisionDocId ? paragraphAt(paras, s.char_start)?.n ?? null : null,
      note: s.crosscheck,
    }));
}

export type ProjectionRow = { key: string; variable: string; period: string; value: string; para: number | null; label: string | null };

const VARIABLE: Record<string, string> = { "rbi.proj_cpi": "CPI inflation", "rbi.proj_gdp": "Real GDP growth" };

/** The resolution's CPI and GDP projections, ranges as ranges, each with its ¶ (or its "AI-extracted" label). */
export function projectionRows(rows: readonly ForecastRow[], paras: readonly Paragraph[], decisionDocId: number | null): ProjectionRow[] {
  return rows.map((f, i) => ({
    key: `${f.series_key}-${f.target_label}-${i}`,
    variable: VARIABLE[f.series_key] ?? f.series_name,
    period: f.target_label,
    value: f.value_low !== null ? formatProjection(f.value_low, f.value_high) : formatProjection(f.value),
    para: f.document_id === decisionDocId ? paragraphAt(paras, f.char_start)?.n ?? null : null,
    label: f.label,
  }));
}

export type VoteRow = { key: string; member: string; vote: string; stance: string | null; dissent: string | null };

/** VoteGrid's rows (Design 01 §4.5): the member, their vote, the RBI stance vote, and a dissent in words. */
export function voteRows(m: PolicyMeetingRow, votes: readonly PolicyVoteRow[]): VoteRow[] {
  const decision = m.policy_rate_change_bps;
  return votes
    .filter((v) => v.meeting_slug === m.slug)
    .map((v) => {
      const why: string[] = [];
      if (v.is_dissent || (decision !== null && v.voted_change_bps !== null && v.voted_change_bps !== decision)) {
        if (v.voted_change_bps === null) why.push("not on the rate");
        else if (v.voted_change_bps !== decision) why.push(v.voted_change_bps === 0 ? "preferred to hold" : `preferred ${formatBpsChange(v.voted_change_bps)}`);
        if (m.bank === "RBI" && v.stance_vote !== null && v.voted_change_bps === decision) {
          why.push(v.stance_vote === "against" ? "voted against the stance" : `stance: voted ${v.stance_vote}`);
        }
      }
      return {
        key: v.member_slug,
        member: v.member_name,
        vote: formatAxisChange(v.voted_change_bps), // the column header carries "(bps)"
        stance: m.bank === "RBI" ? (v.stance_vote === null ? NOT_AVAILABLE : v.stance_vote === "against" ? "Against" : stanceText(v.stance_vote)) : null,
        dissent: why.length || v.is_dissent ? why.join("; ") || "dissented" : null,
      };
    })
    .sort((a, b) => Number(a.dissent !== null) - Number(b.dissent !== null) || a.member.localeCompare(b.member));
}

/** The member statements in minutes: each speaker's first paragraph, for the Minutes tab's anchors. */
export function speakerAnchors(chunks: readonly { char_start: number; speaker: string | null }[], paras: readonly Paragraph[]): { speaker: string; para: number }[] {
  const seen = new Set<string>();
  const out: { speaker: string; para: number }[] = [];
  for (const c of [...chunks].sort((a, b) => a.char_start - b.char_start)) {
    if (!c.speaker || seen.has(c.speaker)) continue;
    const p = paragraphAt(paras, c.char_start);
    if (!p) continue;
    seen.add(c.speaker);
    out.push({ speaker: c.speaker, para: p.n });
  }
  return out;
}
