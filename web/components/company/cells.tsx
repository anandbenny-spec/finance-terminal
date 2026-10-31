// The company page's small shared pieces (2A-19): how an empty figure is told, and how a stored amount is formatted
// for its currency. Every number goes through lib/format.ts; nothing here formats inline.
import {
  formatFraction,
  formatMoney,
  formatPrice,
  formatRupeesAsCrore,
  NOT_APPLICABLE,
  NOT_AVAILABLE,
  NOT_AVAILABLE_LABEL,
  type Currency,
} from "@/lib/format";
import type { LineSpec } from "@/lib/equity/company";

/** An empty figure never reads blank: "Held" or "N/A" with the stored reason (tooltip and screen readers), or a dash labelled "Not available". */
export function EmptyFigure({ state, reason }: { state: "held" | "not_applicable" | "missing"; reason?: string | null }) {
  if (state === "missing") {
    return (
      <span title={NOT_AVAILABLE_LABEL}>
        <span aria-hidden>{NOT_AVAILABLE}</span>
        <span className="sr-only">{NOT_AVAILABLE_LABEL}</span>
      </span>
    );
  }
  const word = state === "not_applicable" ? NOT_APPLICABLE : "Held";
  return (
    <span title={reason ?? undefined}>
      <span aria-hidden>{word}</span>
      <span className="sr-only">
        {word}
        {reason ? `: ${reason}` : ""}
      </span>
    </span>
  );
}

/** A stored amount in full units of its reporting currency: crore for rupees, whole dollars otherwise. */
export function amount(value: number | null, currency: Currency): string {
  return currency === "INR" ? formatRupeesAsCrore(value) : formatMoney(value, currency);
}

/** A statement line's value by its kind. */
export function lineValue(spec: LineSpec, value: number | null, currency: Currency): string {
  if (spec.kind === "price") return formatPrice(value, currency);
  if (spec.kind === "ratio") return formatFraction(value, 2);
  return amount(value, currency);
}
