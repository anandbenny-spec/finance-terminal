// Sidebar (Design 01 §4.1, §5.5): 232 px from xl, a 56 px rail of icons with tooltips at lg, and below lg the
// drawer opened from the top bar's menu button (MobileNav). Live modules only (lib/shell/nav.ts).
import { Suspense } from "react";
import { NavLinks, NavList } from "@/components/shell/Nav";

export function Sidebar() {
  return (
    <nav
      aria-label="Main"
      className="hidden w-(--rail-w) shrink-0 border-r border-line bg-panel px-2 py-3 lg:block xl:w-(--sidebar-w)"
    >
      <Suspense fallback={<NavList active={null} variant="sidebar" />}>
        <NavLinks variant="sidebar" />
      </Suspense>
    </nav>
  );
}
