// web/lib/labelling/tone.ts: the tone gate's labelling rules (Spec 02B §5.8; job 2B-12, #175). Pure.
//
// - The set `earnings_tone` is 30 management passages from earnings calls. Their text is private, so it comes from
//   `GET /api/admin/labelling/items` (admin token checked there), never from a Supabase read in the browser.
// - Blind: the reply holds each passage's id, section and text only; no company, quarter, result or model output.
// - Labels stay in this browser (localStorage, best effort) and leave as one exported JSON file,
//   `earnings_tone.labels.json`, which Anand commits under py/config/eval/sets/.
import { REASON_MAX, loadLabels, storeLabels, type Labels } from "@/lib/labelling/labelling";

export { loadLabels, storeLabels, type Labels };
export const TONE_SET = "earnings_tone";
export const TONE_SCORES = ["-2", "-1", "0", "+1", "+2"] as const;
export type ToneItem = { id: string; section: "opening" | "qa" | "closing"; text: string };
export type ToneItems = { count: number; items: ToneItem[] };

export const SECTION_LABEL: Record<ToneItem["section"], string> = {
  opening: "Opening remarks",
  qa: "An answer in the Q&A",
  closing: "Closing remarks",
};

/** The route's reply, checked: anything else gives null rather than a broken page. */
export function parseToneItems(raw: unknown): ToneItems | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { set?: unknown; count?: unknown; items?: unknown };
  if (r.set !== TONE_SET || typeof r.count !== "number" || !Array.isArray(r.items)) return null;
  const items: ToneItem[] = [];
  for (const it of r.items as Partial<ToneItem>[]) {
    if (typeof it?.id !== "string" || typeof it.text !== "string") return null;
    if (it.section !== "opening" && it.section !== "qa" && it.section !== "closing") return null;
    items.push({ id: it.id, section: it.section, text: it.text });
  }
  return { count: r.count, items };
}

export const toneStorageKey = (ruleVersion = 1) => `ft-labels:${TONE_SET}:v${ruleVersion}`;

/** A label is complete with a score from the rubric and a one-line reason. */
export function validToneLabel(label: string, reason: string): boolean {
  const r = reason.trim();
  return (TONE_SCORES as readonly string[]).includes(label) && r.length > 0 && r.length <= REASON_MAX && !/[\r\n]/.test(r);
}

/** The exported file: the items in order with the label and reason (unlabelled ones are listed too). */
export function toneExport(items: readonly ToneItem[], labels: Labels, now: string) {
  return {
    set: TONE_SET,
    rule_version: 1,
    labelled_by: "Anand",
    exported_at: now,
    complete: items.every((it) => labels[it.id]),
    labels: items.map((it) => ({
      id: it.id,
      section: it.section,
      label: labels[it.id]?.label ?? null,
      reason: labels[it.id]?.reason ?? null,
    })),
  };
}

/** The first item without a label (or the last, when all are labelled). */
export function nextUnlabelledTone(items: readonly ToneItem[], labels: Labels): number {
  const i = items.findIndex((it) => !labels[it.id]);
  return i === -1 ? Math.max(items.length - 1, 0) : i;
}
