// Footer (Design 01 §4.1), at the end of main on every page: the disclaimer (CLAUDE.md §1), the sources a page
// names, and the link Lightweight Charts' licence asks for (its logo stays off the charts; web/NOTICE carries
// the notice). "How it's built" joins when that page is live (stage 2).
import Link from "next/link";

export function Footer({ sources }: { sources?: string[] }) {
  return (
    <footer className="mt-6 flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-3 text-meta text-ink-3">
      <span>Informational only, not investment advice.</span>
      {sources && sources.length > 0 && <span>Sources: {sources.join(" · ")}</span>}
      <Link href="/about" className="underline">
        About and privacy
      </Link>
      <Link href="/accuracy" className="underline">
        Accuracy
      </Link>
      <a href="https://www.tradingview.com/" className="underline">
        Charts: TradingView Lightweight Charts
      </a>
    </footer>
  );
}
