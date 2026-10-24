// web/lib/shell/nav.ts: the sidebar's groups (Design 01 §1.11, §4.1; Spec 01 §4.1). Live pages only: a page joins
// the list when it ships, never as "coming soon". Start here (the landing page, 3–12 Nov), Policy Day (Pass 1b)
// and How it's built (stage 2) join then. The admin group shows only to the logged-in admin (C6 builds the login).

export type NavIcon = "policy" | "admin" | "health" | "labelling";

export type NavItem = { href: string; label: string; icon: NavIcon };

export type NavGroup = { heading: string; items: NavItem[]; adminOnly?: boolean };

export const NAV: readonly NavGroup[] = [
  { heading: "Macro and policy", items: [{ href: "/policy", label: "Fed vs RBI", icon: "policy" }] },
  {
    heading: "Admin",
    adminOnly: true,
    items: [
      { href: "/admin", label: "Admin", icon: "admin" },
      { href: "/admin/health", label: "Health", icon: "health" },
      { href: "/admin/labelling", label: "Labelling", icon: "labelling" },
    ],
  },
];

/** The groups a visitor sees: the admin group only for the admin. */
export function visibleGroups(isAdmin: boolean, nav: readonly NavGroup[] = NAV): NavGroup[] {
  return nav.filter((g) => !g.adminOnly || isAdmin);
}

/**
 * The item a path belongs to: the longest href that is the path or a parent of it, so /policy/fed/2026-10-28
 * marks Fed vs RBI, and /admin/health marks Health rather than Admin. Null when none matches.
 */
export function activeHref(pathname: string | null, nav: readonly NavGroup[] = NAV): string | null {
  if (!pathname) return null;
  let best: string | null = null;
  for (const { href } of nav.flatMap((g) => g.items)) {
    const matches = pathname === href || pathname.startsWith(`${href}/`);
    if (matches && (!best || href.length > best.length)) best = href;
  }
  return best;
}
