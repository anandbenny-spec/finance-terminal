// Tabs (Design 01 §4.2, §4.4): a row of links that put the current tab in the URL (?tab=… on a meeting, ?period=… on
// /accuracy). The shared form of what MeetingView built for itself (extracted at its second use: CLAUDE.md §6 rule 2,
// #135): the markup and classes are the same, so the meeting pages look and test as before. The caller builds each
// href; `current` marks one tab with aria-current="page". Server-rendered: no state, no script.
import Link from "next/link";

export type TabItem = { id: string; label: string; href: string };

export function Tabs({ label, items, current }: { label: string; items: readonly TabItem[]; current: string }) {
  return (
    <nav aria-label={label} className="mt-4 overflow-x-auto border-b border-line-strong">
      <ul className="flex min-w-max gap-4">
        {items.map((t) => (
          <li key={t.id}>
            <Link
              href={t.href}
              aria-current={t.id === current ? "page" : undefined}
              className="-mb-px inline-block border-b-2 border-transparent py-2 text-ui text-ink-2 no-underline hover:text-ink-1 aria-[current=page]:border-amber aria-[current=page]:text-ink-1"
            >
              {t.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
