// web/lib/labelling/labelling.ts: the labelling page's rules (Prompt 01 E1; Architecture 03 §1.9). Pure.
//
// - The items are py/config/eval/sets/labelling_items.json, chosen by py ft/eval/labelling.py; ./items.json is a
//   byte-identical copy (a pytest keeps them equal), since the web build can't import from py/.
// - No model output: the page reads documents, chunks and rate settings only, never ai_outputs, and never the
//   stance or score columns. RBI's drafted stance (Claude's, for Anand to check) is shown only once his own label
//   is saved, so it can't anchor him.
// - Labels stay in this browser (localStorage, best effort) and leave as one exported JSON file per set, which
//   Anand commits as py/config/eval/sets/<set>.labels.json (no table: that would be a migration).
import type { DocumentChunkRow, PolicyRateSettingDetailRow } from "@/lib/queries/api";
import { paragraphAt, paragraphs, sliceCodePoints, type Paragraph } from "@/lib/policy/text";

export type SetName = "policy_hawk_dove" | "policy_stance" | "policy_member_hawk_dove";
export const SET_NAMES: readonly SetName[] = ["policy_hawk_dove", "policy_member_hawk_dove", "policy_stance"];

export const SET_TITLE: Record<SetName, string> = {
  policy_hawk_dove: "Hawk/dove: statements and resolutions",
  policy_member_hawk_dove: "Hawk/dove: MPC members' statements",
  policy_stance: "Stance",
};

export type Item = {
  id: string;
  bank: "Fed" | "RBI";
  meeting: string;
  decision_date: string;
  doc_type: "fomc_statement" | "mpc_resolution" | "mpc_minutes";
  phase: string;
  speaker?: string;
};

export type ItemsFile = {
  rule_version: number;
  chosen_from: string | null;
  sets: Record<SetName, Item[]>;
  rbi_stance_drafts: Record<string, { label: string; reason?: string }>;
};

export type Label = { label: string; reason: string; saved_at: string };
export type Labels = Record<string, Label>;

/** What a label may be, by set and bank (Spec 01 §3.3 for the RBI stance list). */
export const HAWK_DOVE_SCORES = ["-2", "-1", "0", "+1", "+2"] as const;
export const FED_GUIDANCE = ["easing bias", "neutral", "tightening bias"] as const;
export const RBI_STANCES = [
  "calibrated tightening",
  "neutral",
  "accommodative",
  "accommodative, focused on withdrawal of accommodation",
  "withdrawal of accommodation",
  "other",
] as const;

export function choices(set: SetName, item: Pick<Item, "bank">): readonly string[] {
  if (set !== "policy_stance") return HAWK_DOVE_SCORES;
  return item.bank === "Fed" ? FED_GUIDANCE : RBI_STANCES;
}

export const REASON_MAX = 200;

/** A label is complete with a valid choice and a one-line reason. */
export function validLabel(set: SetName, item: Pick<Item, "bank">, label: string, reason: string): boolean {
  const r = reason.trim();
  return choices(set, item).includes(label) && r.length > 0 && r.length <= REASON_MAX && !/[\r\n]/.test(r);
}

/** The items file, checked: a malformed one gives no items rather than a broken page. */
export function parseItems(raw: unknown): ItemsFile | null {
  if (!raw || typeof raw !== "object") return null;
  const f = raw as Partial<ItemsFile>;
  if (typeof f.rule_version !== "number" || !f.sets || typeof f.sets !== "object") return null;
  for (const name of SET_NAMES) {
    const items = (f.sets as Record<string, unknown>)[name];
    if (!Array.isArray(items)) return null;
    for (const it of items as Partial<Item>[]) {
      if (typeof it?.id !== "string" || (it.bank !== "Fed" && it.bank !== "RBI") || typeof it.meeting !== "string") return null;
      if (it.doc_type !== "fomc_statement" && it.doc_type !== "mpc_resolution" && it.doc_type !== "mpc_minutes") return null;
      if (it.doc_type === "mpc_minutes" && typeof it.speaker !== "string") return null;
    }
  }
  return { chosen_from: null, rbi_stance_drafts: {}, ...f } as ItemsFile;
}

export const storageKey = (set: SetName, ruleVersion: number) => `ft-labels:${set}:v${ruleVersion}`;

export function loadLabels(storage: Pick<Storage, "getItem"> | undefined, key: string): Labels {
  try {
    const raw = storage?.getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Labels) : {};
  } catch {
    return {};
  }
}

/** Saves; false when the browser refused (the page then says to export now). */
export function storeLabels(storage: Pick<Storage, "setItem"> | undefined, key: string, labels: Labels): boolean {
  try {
    if (!storage) return false;
    storage.setItem(key, JSON.stringify(labels));
    return true;
  } catch {
    return false;
  }
}

/** The exported file: the set's items in order with Anand's label and reason (unlabelled items are listed too). */
export function exportPayload(file: ItemsFile, set: SetName, labels: Labels, now: string) {
  const items = file.sets[set];
  return {
    set,
    rule_version: file.rule_version,
    labelled_by: "Anand",
    exported_at: now,
    complete: items.every((it) => labels[it.id]),
    labels: items.map((it) => ({
      id: it.id,
      bank: it.bank,
      meeting: it.meeting,
      ...(it.speaker ? { speaker: it.speaker } : {}),
      label: labels[it.id]?.label ?? null,
      reason: labels[it.id]?.reason ?? null,
    })),
  };
}

/** The first item without a label (or the last, when all are labelled). */
export function nextUnlabelled(items: readonly Item[], labels: Labels): number {
  const i = items.findIndex((it) => !labels[it.id]);
  return i === -1 ? Math.max(items.length - 1, 0) : i;
}

export type ItemText = {
  /** The text shown: the decision document, or the member's own statement from the minutes. */
  text: string;
  paras: Paragraph[];
  /** The paragraph highlighted, or null when none could be found (the page says so). */
  highlight: Paragraph | null;
};

const RATE_INSTRUMENT = { Fed: "fed_target_upper", RBI: "repo" } as const;

/**
 * The decision document's text with its decision paragraph: the one holding the policy-rate setting's stored span
 * (the Fed's target range, the RBI's repo rate), as the meeting page's quoted paragraph is found (C4, D2).
 */
export function decisionText(
  bank: Item["bank"],
  documentId: number,
  text: string,
  settings: readonly Pick<PolicyRateSettingDetailRow, "instrument" | "document_id" | "char_start">[],
): ItemText {
  const paras = paragraphs(text);
  const s = settings.find((r) => r.instrument === RATE_INSTRUMENT[bank] && r.document_id === documentId);
  return { text, paras, highlight: s ? paragraphAt(paras, s.char_start) : null };
}

/**
 * One member's statement from the minutes: the text from their first chunk to their last, with the paragraph
 * where they state their vote highlighted (the first that says "vote"). Null when the minutes hold no chunk for them.
 */
export function memberText(fullText: string, chunks: readonly DocumentChunkRow[], speaker: string): ItemText | null {
  const own = chunks.filter((c) => c.speaker === speaker).sort((a, b) => a.chunk_no - b.chunk_no);
  if (own.length === 0) return null;
  const text = sliceCodePoints(fullText, own[0].char_start, own[own.length - 1].char_end);
  const paras = paragraphs(text);
  return { text, paras, highlight: paras.find((p) => /\bvot(e|ed|ing)\b/i.test(p.text)) ?? null };
}
