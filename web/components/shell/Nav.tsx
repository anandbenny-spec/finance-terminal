"use client";

// The sidebar's links (Design 01 §4.1; Spec 01 §4.1), shared by the sidebar (expanded at xl, a rail of icons with
// tooltips at lg) and the drawer below lg. Active item: a 2 px amber bar on the left, the amber wash, ink-1.
// usePathname suspends on a route with an unknown dynamic param under Cache Components, so the sidebar renders
// `NavLinks` inside Suspense with a fallback that marks nothing active (Next 16 docs, usePathname).
import { HeartPulse, Landmark, LayoutDashboard, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "cn";
import { useAdmin } from "@/components/shell/admin";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { activeHref, visibleGroups, type NavIcon } from "@/lib/shell/nav";

const ICONS: Record<NavIcon, LucideIcon> = { policy: Landmark, admin: LayoutDashboard, health: HeartPulse };

type Variant = "sidebar" | "drawer";

export function NavList({
  active,
  variant,
  onNavigate,
}: {
  active: string | null;
  variant: Variant;
  onNavigate?: () => void;
}) {
  const { isAdmin } = useAdmin();
  const rail = variant === "sidebar"; // a rail between lg and xl; expanded from xl
  return (
    <div className="flex flex-col">
      {visibleGroups(isAdmin).map((group) => (
        <div key={group.heading} className="mt-4 flex flex-col first:mt-1">
          <h2 className={cn("mx-2 mb-1.5 text-meta text-ink-3", rail && "lg:max-xl:sr-only")}>
            {group.heading}
          </h2>
          <ul className="flex flex-col gap-0.5">
            {group.items.map((item) => {
              const Icon = ICONS[item.icon];
              const current = item.href === active;
              const link = (
                <Link
                  href={item.href}
                  onClick={onNavigate}
                  aria-current={current ? "page" : undefined}
                  className={cn(
                    "flex h-8 items-center gap-2.5 rounded-md border-l-2 border-transparent px-2.5 text-ui text-ink-2 no-underline",
                    "hover:bg-raised hover:text-ink-1",
                    "aria-[current=page]:rounded-l-none aria-[current=page]:border-amber aria-[current=page]:bg-amber-wash aria-[current=page]:text-ink-1",
                    rail && "lg:max-xl:justify-center lg:max-xl:px-0",
                  )}
                >
                  <Icon aria-hidden className="size-4 shrink-0" />
                  <span className={cn(rail && "lg:max-xl:sr-only")}>{item.label}</span>
                </Link>
              );
              return (
                <li key={item.href}>
                  {rail ? (
                    <Tooltip>
                      <TooltipTrigger asChild>{link}</TooltipTrigger>
                      {/* Only the rail needs it: at xl the label is on screen. */}
                      <TooltipContent side="right" className="hidden lg:max-xl:block">
                        {item.label}
                      </TooltipContent>
                    </Tooltip>
                  ) : (
                    link
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
export function NavLinks({ variant, onNavigate }: { variant: Variant; onNavigate?: () => void }) {
  return <NavList active={activeHref(usePathname())} variant={variant} onNavigate={onNavigate} />;
}
