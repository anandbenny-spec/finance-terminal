// web/lib/policy/changes.ts: "What changed" for one bank's decision, assembled from the rows the database returned
// (Design 01 §1.16 layers 1-2). The meeting page's What changed tab and the front page (#152, the second use) both
// call it, so the two always show the same summary, Key changes and numbers. Pure: no reads, no rendering. Every word
// is one the stored diff, the cited summary or the stored rows hold.
import { dateInZone, formatDate } from "@/lib/format";
import { summaryBlock, type AiBlockData } from "@/lib/policy/ai";
import { buildKeyChanges, type KeyChanges } from "@/lib/policy/keychanges";
import { ZONE } from "@/lib/policy/meeting";
import { numbersChanged, type NumberItem } from "@/lib/policy/numbers";
import { parsePairs, buildRedline, type Redline, type RedlinePair } from "@/lib/policy/redline";
import type { DocumentDiffRow, DocumentRow, ForecastRow, PolicyMeetingRow, PolicyRateSettingDetailRow, PolicyVoteRow } from "@/lib/queries/api";
import type { CitedOutput } from "@/lib/queries/meeting";
import type { Bank } from "@/lib/queries/policy";

/** The day a document was published, in the bank's zone (its "Data to"), else the meeting's date. */
export function docDateOf(bank: Bank, m: Pick<PolicyMeetingRow, "decision_date">, d: DocumentRow | null | undefined): string {
  return d?.published_at ? dateInZone(d.published_at, ZONE[bank]) : m.decision_date;
}

/** What a stored diff, its base document and its cited summary say about one decision, ready to show. */
export type ChangesModel = {
  /** "FOMC statement of 30 Jul 2026", or "the previous one" when the base document can't be read. */
  baseLabel: string;
  /** The compared documents' dates, for the panel's qualifier: "17 Sep 2026 vs 30 Jul 2026". */
  qualifier: string;
  before: { href: string; date: string };
  now: { href: string; date: string };
  /** The cited "What changed" summary; null when it has no verified citation (the caller shows its StateNote). */
  summary: AiBlockData | null;
  /** Null when the stored segments can't be read as sentence pairs (no redline, no Key changes). */
  pairs: RedlinePair[] | null;
  keys: KeyChanges | null;
  redline: Redline | null;
  wordsAdded: number;
  wordsRemoved: number;
};

export function changesModel({
  bank,
  m,
  decisionDoc,
  diff,
  base,
  summary,
}: {
  bank: Bank;
  m: Pick<PolicyMeetingRow, "decision_date">;
  decisionDoc: DocumentRow;
  diff: DocumentDiffRow;
  base: DocumentRow | null;
  summary: CitedOutput | null;
}): ChangesModel {
  const pairs = parsePairs(diff.segments);
  const nowDate = formatDate(docDateOf(bank, m, decisionDoc));
  const baseDate = base ? formatDate(docDateOf(bank, m, base)) : null;
  return {
    baseLabel: base ? `${base.doc_type_label} of ${baseDate}` : "the previous one",
    qualifier: baseDate ? `${nowDate} vs ${baseDate}` : nowDate,
    before: { href: `/documents/${diff.base_document_id}`, date: baseDate ?? "before" },
    now: { href: `/documents/${decisionDoc.id}`, date: nowDate },
    summary: summary ? summaryBlock(summary.output, summary.citations, { linkOfficial: bank === "Fed", label: "What changed" }) : null,
    pairs,
    keys: pairs ? buildKeyChanges(pairs) : null,
    redline: pairs ? buildRedline(pairs) : null,
    wordsAdded: diff.words_added,
    wordsRemoved: diff.words_removed,
  };
}

/** Numbers that changed, against the meeting the diff's base document belongs to (the same target period across vintages). */
export type NumbersModel =
  | { kind: "none" }
  | { kind: "error" }
  | { kind: "unavailable" }
  | { kind: "items"; items: NumberItem[]; previousDate: string; newDate: string };

export type NumbersEarlier = { meeting: PolicyMeetingRow | null; settings: PolicyRateSettingDetailRow[]; votes: PolicyVoteRow[]; projections: ForecastRow[] };

export function numbersModel({
  m,
  base,
  current,
  earlier,
  stance,
}: {
  m: PolicyMeetingRow;
  base: DocumentRow | null;
  /** The decision's own rows; null when any of the three reads failed. */
  current: { settings: PolicyRateSettingDetailRow[]; votes: PolicyVoteRow[]; projections: ForecastRow[] } | null;
  /** The earlier meeting's rows; null when that read failed. */
  earlier: NumbersEarlier | null;
  /** The stance's field policy, looked up by the caller: show it (and with which label), or hold it back. */
  stance: { show: boolean; label: string | null };
}): NumbersModel {
  if (!base?.event_slug) return { kind: "none" };
  if (!current || !earlier) return { kind: "error" };
  if (!earlier.meeting) return { kind: "none" };
  const items = numbersChanged({
    current: m,
    previous: earlier.meeting,
    currentSettings: current.settings,
    previousSettings: earlier.settings,
    currentProjections: current.projections,
    previousProjections: earlier.projections,
    currentVotes: current.votes,
    previousVotes: earlier.votes,
    stance,
  });
  return items.length === 0 ? { kind: "unavailable" } : { kind: "items", items, previousDate: earlier.meeting.decision_date, newDate: m.decision_date };
}
