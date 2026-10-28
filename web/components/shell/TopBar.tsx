// TopBar (Design 01 §4.1): 48 px, sticky, layer 20. The labelled Menu button (below lg) · the wordmark ("Finance Terminal"
// in Plex Mono 500 after an 8 px amber square) · the command bar's slot, centred, at most 640 px · the admin badge.
// At 320 px the admin's row is Menu 79 + wordmark 151 + badge 28 (icon only below sm) + two 12 px gaps + 32 px of padding
// = 314 px (#147): a visitor's has room to spare; the word "Admin" returns at sm, and the gaps at 16 px.
// The command bar itself is built 3–12 Nov (Spec 01 §6); until then its slot stays empty rather than showing a
// search box that does nothing, and the mobile search button and ⌘K arrive with it.
import Link from "next/link";
import { AdminBadge } from "@/components/shell/admin";
import { MobileNav } from "@/components/shell/MobileNav";

export function TopBar() {
  return (
    <header className="sticky top-0 z-20 flex h-(--topbar-h) shrink-0 items-center gap-3 border-b border-line bg-panel px-4 sm:gap-4">
      <MobileNav />
      <Link
        href="/"
        className="flex items-center gap-2 font-mono text-ui font-medium whitespace-nowrap text-ink-1 no-underline lg:min-w-50"
      >
        <span aria-hidden className="size-2 shrink-0 bg-amber" />
        Finance Terminal
      </Link>
      <div data-slot="command-bar" className="mx-auto hidden max-w-160 flex-1 md:block" />
      <div className="ml-auto flex items-center justify-end md:ml-0 lg:min-w-50">
        <AdminBadge />
      </div>
    </header>
  );
}
