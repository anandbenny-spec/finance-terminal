// web/lib/policy/keychanges.ts: Key changes' model (Design 01 §1.16 layer 2, §4.5 KeyChanges) from the stored diff's
// pairs: the changed, added and removed sentences grouped by topic (py ft/policy/topics.py fills each pair's topic;
// "Other" is the fallback), each with its Before and Now sides and the paragraph (¶) each side sits in. Pure. Every
// word is one the stored diff holds: Before is the pair's eq + del words, Now its eq + ins words.
import type { Op, RedlinePair } from "@/lib/policy/redline";

export const OTHER_TOPIC = "Other";

/** One side of a pair: its words (with the struck/underlined runs marked), or null when the sentence was added/removed. */
export type KeySide = { ops: Op[]; para: number | null } | null;

export type KeyRow = { key: string; kind: "changed" | "added" | "removed"; before: KeySide; now: KeySide };
export type KeyGroup = { topic: string; rows: KeyRow[] };
export type KeyChanges = { groups: KeyGroup[]; changedSentences: number; otherSentences: number };

/** The topics' display order: the document's own order, "Other" last; a topic the list doesn't know follows. */
const ORDER = [
  "Decision and guidance", "Decision and stance", "Economy and labour market", "Inflation", "Growth", "Growth and inflation",
  "Global", "Domestic", "Goals and risks", "Balance sheet", "Monitoring", "Rationale", "Votes",
];

function rank(topic: string): number {
  if (topic === OTHER_TOPIC) return 1e6;
  const i = ORDER.indexOf(topic);
  return i === -1 ? 1000 : i;
}

export function buildKeyChanges(pairs: readonly RedlinePair[]): KeyChanges {
  const by = new Map<string, KeyRow[]>();
  let n = 0;
  pairs.forEach((p, i) => {
    if (p.kind === "same") return;
    const topic = p.topic ?? OTHER_TOPIC;
    const row: KeyRow =
      p.kind === "changed"
        ? {
            key: `${i}`,
            kind: "changed",
            before: { ops: p.ops.filter((o) => o.op !== "ins"), para: p.oldPara },
            now: { ops: p.ops.filter((o) => o.op !== "del"), para: p.newPara },
          }
        : p.kind === "added"
          ? { key: `${i}`, kind: "added", before: null, now: { ops: [{ op: "ins", text: p.text }], para: p.newPara } }
          : { key: `${i}`, kind: "removed", before: { ops: [{ op: "del", text: p.text }], para: p.oldPara }, now: null };
    by.set(topic, [...(by.get(topic) ?? []), row]);
    n += 1;
  });
  const groups = [...by.entries()].map(([topic, rows]) => ({ topic, rows })).sort((a, b) => rank(a.topic) - rank(b.topic));
  return { groups, changedSentences: n, otherSentences: by.get(OTHER_TOPIC)?.length ?? 0 };
}

/**
 * The first `n` changed sentences in Key changes' own order (the topics' display order, then the document's), kept in
 * their topic groups, and how many other sentences changed. No ranking of our own: the front page (#152) shows what
 * the tab shows first.
 */
export function previewKeyChanges(model: KeyChanges, n: number): { groups: KeyGroup[]; shown: number; others: number } {
  const limit = Math.max(0, Math.floor(n));
  const groups: KeyGroup[] = [];
  let shown = 0;
  for (const g of model.groups) {
    if (shown >= limit) break;
    const rows = g.rows.slice(0, limit - shown);
    groups.push({ topic: g.topic, rows });
    shown += rows.length;
  }
  return { groups, shown, others: model.changedSentences - shown };
}
