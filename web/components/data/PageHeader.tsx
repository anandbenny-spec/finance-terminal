// PageHeader (Design 01 §4.2): breadcrumb (ink-3), the page title, a one-line description (ink-2) and a meta row of
// sources and freshness. The document title ("Page · Finance Terminal") is set by the page's metadata.
import type { ReactNode } from "react";

export function PageHeader({
  crumbs,
  title,
  description,
  meta,
}: {
  crumbs: string[];
  title: string;
  description: string;
  meta?: ReactNode;
}) {
  return (
    <header>
      <nav aria-label="Breadcrumb" className="mb-1.5 text-meta text-ink-3">
        <ol className="flex flex-wrap gap-1">
          {crumbs.map((c, i) => (
            <li key={c} className="flex gap-1">
              {i > 0 && <span aria-hidden>›</span>}
              <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c}</span>
            </li>
          ))}
        </ol>
      </nav>
      <h1 className="text-page text-ink-1">{title}</h1>
      <p className="mt-1 mb-2 text-ink-2">{description}</p>
      {meta && <div className="flex flex-wrap gap-x-4 gap-y-1 text-meta text-ink-3">{meta}</div>}
    </header>
  );
}
