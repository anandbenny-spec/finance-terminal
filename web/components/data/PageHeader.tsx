// PageHeader (Design 01 §4.2): breadcrumb (ink-3), the page title, a one-line description (ink-2) and a meta row of
// sources and freshness. The document title ("Page · Finance Terminal") is set by the page's metadata.
import Link from "next/link";
import type { ReactNode } from "react";

export type Crumb = string | { label: string; href: string };

export function PageHeader({
  crumbs,
  title,
  description,
  meta,
}: {
  crumbs: Crumb[];
  title: string;
  description?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header>
      <nav aria-label="Breadcrumb" className="mb-1.5 text-meta text-ink-3">
        <ol className="flex flex-wrap gap-1">
          {crumbs.map((c, i) => {
            const label = typeof c === "string" ? c : c.label;
            return (
              <li key={label} className="flex gap-1">
                {i > 0 && <span aria-hidden>›</span>}
                {typeof c === "string" ? (
                  <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c}</span>
                ) : (
                  <Link href={c.href} className="text-ink-3 underline hover:text-ink-1">
                    {c.label}
                  </Link>
                )}
              </li>
            );
          })}
        </ol>
      </nav>
      <h1 className="text-page text-ink-1">{title}</h1>
      {description && <div className="mt-1 mb-2 text-ink-2">{description}</div>}
      {meta && <div className="flex flex-wrap gap-x-4 gap-y-1 text-meta text-ink-3">{meta}</div>}
    </header>
  );
}
