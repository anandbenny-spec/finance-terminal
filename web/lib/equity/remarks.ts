// web/lib/equity/remarks.ts: the Earnings calls tab's "Opening remarks" panel (2B-19d, #388; Spec 02B §5.13, §8) from
// api.remarks_changes (0036). Pure. The browser derives no figure: the share unchanged, the word counts and the speakers are
// the view's; this only reads the stored sentence pairs into Key changes' model. A pair side is shown only as a verified quote
// of the stored transcript (#414): the scoring task located it by code-point range, at most 400 characters; it is cut again here
// by code point (JavaScript counts UTF-16) in case a stored side were longer. An unverified or older side shows nothing.
// Words, never colour: nothing here is a market move (CLAUDE.md §5).
import { formatCount, formatFraction } from "@/lib/format";
import { countsToShares, type RowBar } from "@/lib/policy/changestrip";
import { pageLink, type Citation } from "@/lib/policy/ai";
import type { KeyChanges, KeyGroup, KeyRow, KeySide } from "@/lib/policy/keychanges";
import type { EarningsCallRow, RemarksChangeRow } from "@/lib/queries/api";

export const REMARKS_FIELD = "equity.remarks_change";
export const REMARKS_HELD = "Opening-remarks changes held back until they pass review";
export const REMARKS_TOPIC = "Opening remarks";
export const MAX_SIDE = 400;
export const MAX_PAIRS = 5;

const cap = (text: string): string => {
  const points = Array.from(text);
  return points.length <= MAX_SIDE ? text : points.slice(0, MAX_SIDE - 1).join("").trimEnd() + "…";
};

/** Where a call's transcript can be opened at a page: its document id and its PDF's address (null where the address is not the PDF itself). */
export type RemarksSource = { label: string; documentId: number | null; url: string | null };

/** A call's source for page links: the address is used only for a call whose transcript the exchange filed (not a company-website page). */
export function remarksSource(call: Pick<EarningsCallRow, "fiscal_label" | "transcript_document_id" | "official_url" | "status"> | undefined, fallbackLabel: string): RemarksSource {
  if (!call) return { label: fallbackLabel, documentId: null, url: null };
  const ok = call.status !== "on_company_website" && call.official_url !== null && call.official_url.startsWith("https://");
  return { label: call.fiscal_label, documentId: call.transcript_document_id, url: ok ? call.official_url : null };
}

const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);

/**
 * One pair side as a verified quote, or null. Only a side the scoring task verified against the stored transcript (`verified`
 * true, a document and a code-point range of at most 400) is shown; a side stored before that (text and spans only) or one that
 * fails any check shows nothing. The quote is the stored, verified text, cut again here by code point; the page link is built
 * only for the call's own transcript document.
 */
function verifiedSide(v: unknown, no: number, source: RemarksSource): KeySide {
  if (typeof v !== "object" || v === null) return null;
  const s = v as { text?: unknown; verified?: unknown; document_id?: unknown; start?: unknown; end?: unknown; page?: unknown; page_last?: unknown };
  if (s.verified !== true || typeof s.text !== "string" || s.text.trim() === "") return null;
  if (!isInt(s.document_id) || !isInt(s.start) || !isInt(s.end) || s.start < 0 || s.end <= s.start || s.end - s.start > MAX_SIDE) return null;
  const quote = cap(s.text);
  const first = isInt(s.page) && s.page >= 1 ? s.page : null;
  const last = isInt(s.page_last) && s.page_last >= (first ?? 1) ? s.page_last : first;
  const page =
    first !== null && last !== null && source.url !== null && source.documentId === s.document_id
      ? pageLink({ page_first: first, page_last: last, page_url: `${source.url.replace(/#.*$/, "")}#page=${first}` })
      : null;
  const citation: Citation = {
    no,
    quote,
    documentTitle: `${source.label} call transcript`,
    href: null,
    officialUrl: null,
    textPublic: false,
    page,
  };
  return { ops: [{ op: "eq", text: quote }], para: null, cite: { citation, page } };
}

/**
 * The stored pairs as Key changes' model: one group, each row a changed, added or removed sentence, each side a verified quote
 * with its citation chip and page link, and no ¶ links. A pair with no verified side is left out.
 */
export function remarksKeyChanges(pairs: unknown, sources: { now: RemarksSource; before: RemarksSource }): KeyChanges {
  const rows: KeyRow[] = [];
  let no = 0;
  if (Array.isArray(pairs)) {
    pairs.slice(0, MAX_PAIRS).forEach((raw, i) => {
      if (typeof raw !== "object" || raw === null) return;
      const p = raw as { old?: unknown; new?: unknown };
      const before = verifiedSide(p.old, no + 1, sources.before);
      const now = verifiedSide(p.new, no + (before ? 2 : 1), sources.now);
      if (before === null && now === null) return;
      no += (before ? 1 : 0) + (now ? 1 : 0);
      rows.push({
        key: `${i}`,
        kind: before === null ? "added" : now === null ? "removed" : "changed",
        before,
        now,
      });
    });
  }
  const groups: KeyGroup[] = rows.length === 0 ? [] : [{ topic: REMARKS_TOPIC, rows }];
  return { groups, changedSentences: rows.length, otherSentences: 0 };
}

/** "83.0%": the share of the opening's wording unchanged against the previous quarter's (numbers masked), from the view's fraction. */
export function similarityText(r: Pick<RemarksChangeRow, "similarity">): string {
  return formatFraction(r.similarity);
}

/** "40 words added, 30 removed". */
export function wordsChangedText(r: Pick<RemarksChangeRow, "words_added" | "words_removed">): string {
  return `${formatCount(r.words_added)} ${r.words_added === 1 ? "word" : "words"} added, ${formatCount(r.words_removed)} removed`;
}

/** The new-speaker note, or null: "New speaker in the opening remarks: A, B". */
export function newSpeakerNote(r: Pick<RemarksChangeRow, "new_speakers">): string | null {
  const names = r.new_speakers.filter((n) => n.trim() !== "");
  return names.length === 0 ? null : `New speaker in the opening remarks: ${names.join(", ")}`;
}

/** The comparison of a call, by its slug. */
export function remarksOf(rows: readonly RemarksChangeRow[] | null, callSlug: string): RemarksChangeRow | null {
  return rows?.find((r) => r.call_slug === callSlug) ?? null;
}

/** One bar of the company's strip: a call's opening remarks against the previous quarter's. */
export type RemarksBar = RowBar & { callSlug: string; fiscalLabel: string; baseLabel: string };

/**
 * The strip's bars from the view's own counts (2B-19f, #395): one per published comparison, oldest first, on the call's
 * `period_end`. Shares come from the stored counts (`words_added` over `words_new`, `words_removed` over `words_base`) through
 * the strip's held-back rule; a row whose counts can't be a share, with no period end, or on a date another bar already holds
 * is held back and counted, not drawn. `href` selects the call on the tab.
 */
export function remarksBars(
  rows: readonly RemarksChangeRow[],
  hrefOf: (callSlug: string) => string,
): { bars: RemarksBar[]; withoutBar: number; total: number } {
  const byDate = new Map<string, RemarksBar>();
  for (const r of [...rows].sort((a, b) => (a.period_end ?? "").localeCompare(b.period_end ?? "") || a.call_slug.localeCompare(b.call_slug))) {
    if (r.period_end === null || byDate.has(r.period_end)) continue;
    const s = countsToShares({ wordsAdded: r.words_added, wordsRemoved: r.words_removed, wordsOld: r.words_base, wordsNew: r.words_new });
    if (!s) continue;
    byDate.set(r.period_end, {
      ...s,
      date: r.period_end,
      label: `${r.fiscal_label}, against ${r.base_fiscal_label}`,
      wordsAdded: r.words_added,
      wordsRemoved: r.words_removed,
      href: hrefOf(r.call_slug),
      callSlug: r.call_slug,
      fiscalLabel: r.fiscal_label,
      baseLabel: r.base_fiscal_label,
    });
  }
  const bars = [...byDate.values()];
  return { bars, withoutBar: rows.length - bars.length, total: rows.length };
}
