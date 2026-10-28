"use client";

// The sidebar's links (Design 01 §4.1, §5.5; Spec 01 §4.1), shared by the sidebar (expanded from lg) and the drawer
// below lg. Active item: a 2 px amber bar on the left, the amber wash, ink-1. An item may carry section links (#147):
// a nested list of 28 px rows under it, labels lined up with the item's, never marked current.
// usePathname suspends on a route with an unknown dynamic param under Cache Components, so the sidebar renders
// `NavLinks` inside Suspense with a fallback that marks nothing active (Next 16 docs, usePathname).
import { HeartPulse, Info, Landmark, LayoutDashboard, Tags, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "cn";
import { useAdmin } from "@/components/shell/admin";
import { activeHref, visibleGroups, type NavIcon } from "@/lib/shell/nav";

const ICONS: Record<NavIcon, LucideIcon> = { policy: Landmark, about: Info, admin: LayoutDashboard, health: HeartPulse, labelling: Tags };

export function NavList({ active, onNavigate }: { active: string | null; onNavigate?: () => void }) {
  const { isAdmin } = useAdmin();
  return (
    <div className="flex flex-col">
      {visibleGroups(isAdmin).map((group) => (
        <div key={group.heading} className="mt-4 flex flex-col first:mt-1">
          <h2 className="mx-2 mb-1.5 text-meta text-ink-3">{group.heading}</h2>
          <ul className="flex flex-col gap-0.5">
            {group.items.map((item) => {
              const Icon = ICONS[item.icon];
              const current = item.href === active;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={current ? "page" : undefined}
                    className={cn(
                      "flex h-8 items-center gap-2.5 rounded-md border-l-2 border-transparent px-2.5 text-ui text-ink-2 no-underline",
                      "hover:bg-raised hover:text-ink-1",
                      "aria-[current=page]:rounded-l-none aria-[current=page]:border-amber aria-[current=page]:bg-amber-wash aria-[current=page]:text-ink-1",
                    )}
                  >
                    <Icon aria-hidden className="size-4 shrink-0" />
                    <span>{item.label}</span>
                  </Link>
                  {item.sections && (
                    <ul className="mt-0.5 flex flex-col">
                      {item.sections.map((section) => (
                        <li key={section.href}>
                          {/* pl-9.5 (38 px) is the item's border, padding, icon and gap, so the labels line up. No
                              prefetch: the /latest links are request-time routes, and a link on every page would
                              read the database once per page view. */}
                          <Link
                            href={section.href}
                            prefetch={false}
                            onClick={onNavigate}
                            className="flex h-7 items-center rounded-md pr-2.5 pl-9.5 text-ui text-ink-2 no-underline hover:bg-raised hover:text-ink-1"
                          >
                            {section.label}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** The links with the current page marked; must sit inside Suspense (see the note above). */
export function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return <NavList active={activeHref(usePathname())} onNavigate={onNavigate} />;
}
