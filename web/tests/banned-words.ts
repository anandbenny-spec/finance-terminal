// The banned-words scan (Spec 02A §5.9; Brief §11), defined once for every 2A page test (#124's amendment, 28 Oct 2026):
// whole words and phrases, case-insensitive, over the site's own text (labels, headings, explainers, reason sentences,
// notes), never over text quoted from a source (a corporate action's "Buy Back" subject, a filing's line names).
export const BANNED_WORDS = /\b(overvalued|undervalued|cheap|expensive|upside|fair value|target|buy|sell|credible|credibility|trust|trustworthy|reliable|unreliable|misled|misleading|broke|promise)\b/i;

// Spec 02B §8 (2B-19, #182): the words the earnings-call pages never use in the site's own text (a missed target is a fact
// about a figure, not a verdict on management). Transcript quotes and the AI summaries' cited text are left out of the scan,
// since management's own words may hold them. The Earnings calls tab is scanned for these and for the 2A list, except "target",
// which Spec 02B's own panel wording ("measurable targets") uses; that panel says "Guidance" in its title.
export const CALLS_BANNED_WORDS = /\b(credible|credibility|trust|trustworthy|reliable|unreliable|misled|misleading|broke|promise)\b/i;

/** The words banned on the earnings-call pages found in `text`, lower-cased, each once: the 2A list without "target", plus §8's. */
export function bannedCallWordsIn(text: string): string[] {
  return bannedWordsIn(text).filter((w) => w !== "target");
}

/** The banned words found in `text`, lower-cased, each once. */
export function bannedWordsIn(text: string): string[] {
  const found = new Set<string>();
  const re = new RegExp(BANNED_WORDS.source, "gi");
  for (const m of text.matchAll(re)) found.add(m[1].toLowerCase());
  return [...found];
}
