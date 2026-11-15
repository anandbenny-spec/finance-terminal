// The screener's market control (US-06, #405; Spec 02A.1 §7): India or the US, one market at a time, so no table ever
// sorts rupees against dollars. The screener's own chip pattern (the index chips' look), as links: the market is in the
// URL (`/screener?market=us`), so a view can be linked and each market's rows, sources and freshness are chosen on the
// server. The page shows this only when the views return a US row (after Anand's flip), so until then /screener is unchanged.
import Link from "next/link";
import type { Market } from "@/lib/format";

const CHIP = "rounded-sm border border-line-strong px-2.5 py-1 text-meta no-underline";
const CHIP_ON = "bg-highlight-wash text-ink-1";
const CHIP_OFF = "text-ink-2 hover:bg-active";

export const MARKETS: readonly { id: Market; label: string; param: string | null }[] = [
  { id: "IN", label: "India", param: null },
  { id: "US", label: "US", param: "us" },
];

/** The market a `?market=` value names, when the US view exists; India otherwise (the default, and every unknown value). */
export function marketOf(value: string | string[] | undefined, usShown: boolean): Market {
  return usShown && value === "us" ? "US" : "IN";
}

/** The screener's address for a market and view, keeping the other: India and the screener view have no parameter. */
export function screenerHref(market: Market, view: "screener" | "priced-in"): string {
  const params = new URLSearchParams();
  if (market === "US") params.set("market", "us");
  if (view === "priced-in") params.set("view", "priced-in");
  const q = params.toString();
  return q ? `/screener?${q}` : "/screener";
}

export function MarketChips({ current, view }: { current: Market; view: "screener" | "priced-in" }) {
  return (
    <nav aria-label="Market" className="mt-4 flex flex-wrap items-center gap-2">
      <span className="text-meta text-ink-2">Market</span>
      {MARKETS.map((m) => (
        <Link
          key={m.id}
          href={screenerHref(m.id, view)}
          aria-current={m.id === current ? "page" : undefined}
          className={`${CHIP} ${m.id === current ? CHIP_ON : CHIP_OFF}`}
        >
          {m.label}
        </Link>
      ))}
      <span className="text-meta text-ink-3">Each market in its own currency; never compared across.</span>
    </nav>
  );
}
