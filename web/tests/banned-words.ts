// The banned-words scan (Spec 02A §5.9; Brief §11), defined once for every 2A page test (#124's amendment, 28 Oct 2026):
// whole words and phrases, case-insensitive, over the site's own text (labels, headings, explainers, reason sentences,
// notes), never over text quoted from a source (a corporate action's "Buy Back" subject, a filing's line names).
export const BANNED_WORDS = /\b(overvalued|undervalued|cheap|expensive|upside|fair value|target|buy|sell)\b/i;

/** The banned words found in `text`, lower-cased, each once. */
export function bannedWordsIn(text: string): string[] {
  const found = new Set<string>();
  const re = new RegExp(BANNED_WORDS.source, "gi");
  for (const m of text.matchAll(re)) found.add(m[1].toLowerCase());
  return [...found];
}
