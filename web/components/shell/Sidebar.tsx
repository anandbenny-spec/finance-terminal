// Sidebar (Design 01 §4.1, §5.5): 232 px with labels from lg, below lg the drawer opened from the top bar's Menu
// button (MobileNav). The 56 px icons-only rail is gone (Anand, 28 Oct 2026; #147). The column keeps its panel colour
// and right rule for the page's full height, so no blank strip is left; only its contents stick below the top bar,
// at most `--nav-max-h` tall, and scroll inside themselves when the list is taller than the window. Live modules
// only (lib/shell/nav.ts).
import { Suspense } from "react";
import { NavLinks, NavList } from "@/components/shell/Nav";

export function Sidebar() {
  return (
    <nav aria-label="Main" className="hidden w-(--sidebar-w) shrink-0 border-r border-line bg-panel lg:block">
      <div className="sticky top-(--topbar-h) max-h-(--nav-max-h) overflow-y-auto px-2 py-3">
        <Suspense fallback={<NavList active={null} />}>
          <NavLinks />
        </Suspense>
      </div>
    </nav>
  );
}
