// SectionHeading (Design 01 §4.2, §1.15): a section's title with a strong rule running to the right edge, 32 px
// above it and 12 px below. Wraps the section's panels so a screen reader hears the section's name. `scroll-mt-16`
// (the 48 px top bar plus 16) lands a section link's heading just under the sticky top bar (#147).
import type { ReactNode } from "react";

export function Section({ id, title, note, children }: { id: string; title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="mt-8 first:mt-5">
      <h2
        id={id}
        className="mb-3 flex scroll-mt-16 items-center gap-3 text-title font-semibold text-ink-1 after:h-px after:flex-1 after:bg-line-strong"
      >
        {title}
        {note && <span className="text-meta font-normal text-ink-3">{note}</span>}
      </h2>
      {children}
    </section>
  );
}
