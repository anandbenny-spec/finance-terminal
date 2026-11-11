// SectionHeading (Design 01 §4.2, §1.15): a section's title with a strong rule running to the right edge, 32 px
// above it and 12 px below. Wraps the section's panels so a screen reader hears the section's name. `scroll-mt-16`
// (the 48 px top bar plus 16) lands a section link's heading just under the sticky top bar (#147).
import type { ReactNode } from "react";

/** `tour` is the guided tour's fixed target id (`data-tour`, lib/tour.ts; #91). */
export function Section({ id, title, note, tour, children }: { id: string; title: string; note?: ReactNode; tour?: string; children: ReactNode }) {
  return (
    <section data-tour={tour} aria-labelledby={id} className="mt-8 first:mt-5">
      <h2
        id={id}
        className="mb-3 flex scroll-mt-16 flex-wrap items-center gap-x-3 gap-y-1 text-title font-semibold text-ink-1 after:h-px after:flex-1 after:bg-line-strong"
      >
        {title}
        {note && <span className="order-last basis-full text-meta font-normal text-ink-3 md:order-none md:basis-auto">{note}</span>}
      </h2>
      {children}
    </section>
  );
}
