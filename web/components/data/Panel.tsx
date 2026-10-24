// Panel (Design 01 §4.2, §1.15): a 1 px strong outline, 4 px radius, the panel surface. A header bar (40 px, raised,
// a strong rule under it) holds the title and qualifier, with icon actions on the right; an optional subtitle says
// the question the panel answers; the footer carries SourceLine and Freshness. `market` adds the 3 px top edge in
// that market's series colour (cyan US/Fed, violet India/RBI). A panel never shows a blank body: callers put a
// StateNote there when they have nothing else.
import { cn } from "cn";
import type { ReactNode } from "react";

export type PanelSpan = 12 | 8 | 6 | 4;

// Spans on the 12-column grid from md (Design 01 §5.2, §5.5): one column below md.
const SPAN: Record<PanelSpan, string> = {
  12: "md:col-span-12",
  8: "md:col-span-12 xl:col-span-8",
  6: "md:col-span-6",
  4: "md:col-span-6 xl:col-span-4",
};

const EDGE = { US: "border-t-3 border-t-series-1", IN: "border-t-3 border-t-series-2" } as const;

export function Panel({
  title,
  titleId,
  qualifier,
  actions,
  subtitle,
  footer,
  market,
  span = 12,
  variant = "default",
  label,
  className,
  children,
}: {
  title?: ReactNode;
  titleId?: string;
  qualifier?: ReactNode;
  actions?: ReactNode;
  subtitle?: ReactNode;
  footer?: ReactNode;
  market?: "US" | "IN";
  span?: PanelSpan;
  variant?: "default" | "flush" | "prose";
  /** An accessible name for a panel without a title bar (the StatRow). */
  label?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-labelledby={title ? titleId : undefined}
      aria-label={title ? undefined : label}
      className={cn(
        "col-span-1 flex min-w-0 flex-col overflow-hidden rounded-md border border-line-strong bg-panel",
        SPAN[span],
        market && EDGE[market],
        className,
      )}
    >
      {title && (
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line-strong bg-raised pr-2 pl-3">
          <h3 id={titleId} className="truncate text-data font-semibold text-ink-1">
            {title}
          </h3>
          {qualifier}
          {actions && <div className="ml-auto flex items-center gap-0.5">{actions}</div>}
        </div>
      )}
      {subtitle && <p className="px-3 pt-2 text-meta text-ink-2">{subtitle}</p>}
      <div className={cn("min-w-0 flex-1", variant === "flush" ? "p-0" : "p-3", variant === "prose" && "max-w-(--reading-width)")}>
        {children}
      </div>
      {footer && (
        <div className="mt-auto flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-line px-3 py-2 text-meta text-ink-3">
          {footer}
        </div>
      )}
    </section>
  );
}

/** A section's 12-column panel grid (Design 01 §5.2): 16 px gaps, one column below md. */
export function PanelGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-4 md:grid-cols-12">{children}</div>;
}
