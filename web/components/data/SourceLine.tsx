// SourceLine (Design 01 §4.2): the source a panel's figures come from, as `core.source` or the series' own
// attribution writes it ("Source: Federal Reserve Board"; "Source: NSE" on NSE-derived figures). Several sources
// join with " · ". RBI documents are named, never linked (Anand, 24 Oct 2026; Brief §6.2).
export function SourceLine({ lines }: { lines: (string | null | undefined)[] }) {
  const unique = [...new Set(lines.filter((l): l is string => Boolean(l)))];
  if (unique.length === 0) return null;
  return <span>{unique.join(" · ")}</span>;
}
