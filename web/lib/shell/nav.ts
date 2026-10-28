// web/lib/shell/nav.ts: the sidebar's groups (Design 01 §1.11, §4.1; Spec 01 §4.1). Live pages only: a page joins
// the list when it ships, never as "coming soon". Start here (the landing page, 3–12 Nov), Policy Day (Pass 1b)
// and How it's built (stage 2) join then. About (#86) is live: the data ownership statement and privacy note. The admin group shows only to the logged-in admin (C6 builds the login).
//
// An item may carry section links (`sections`, #147): indented rows under it, no icon, never marked current. A
// section link needs a live target like an item does: Fed vs RBI's three are its page's sections
// (lib/policy/sections.ts, so a rename can't break them) and its two "what changed" links are the /latest routes,
// which forward to the newest held meeting. Later jobs (#132, #90, #135) add their items in this shape, after
// merging main, and update nav.test.ts's lists.
import { POLICY_SECTION_LIST, policySectionHref } from "@/lib/policy/sections";

export type NavIcon = "policy" | "about" | "admin" | "health" | "labelling";

/** A link under an item: a label and a live target, no icon. */
export type NavSubLink = { href: string; label: string };

export type NavItem = { href: string; label: string; icon: NavIcon; sections?: readonly NavSubLink[] };

export type NavGroup = { heading: string; items: NavItem[]; adminOnly?: boolean };

export const NAV: readonly NavGroup[] = [
  {
    heading: "Macro and policy",
    items: [
      {
        href: "/policy",
        label: "Fed vs RBI",
        icon: "policy",
        sections: [
          ...POLICY_SECTION_LIST.map((s) => ({ href: policySectionHref(s), label: s.title })),
          { href: "/policy/fed/latest?tab=changes", label: "FOMC: what changed" },
          { href: "/policy/rbi/latest?tab=changes", label: "MPC: what changed" },
        ],
      },
    ],
  },
  { heading: "About", items: [{ href: "/about", label: "About and privacy", icon: "about" }] },
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
 * marks Fed vs RBI, and /admin/health marks Health rather than Admin. Null when none matches. Top-level items only:
 * a section link is never current (no scroll tracking).
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
