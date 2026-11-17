// TopBar (Design 01 §4.1): 48 px, sticky, layer 20. The labelled Menu button (below lg) · the wordmark ("Finance Terminal"
// in Plex Mono 500 after an 8 px highlight square) · the command bar's slot, centred, at most 640 px · the admin badge.
// At 320 px the admin's row is Menu 79 + wordmark 151 + badge 28 (icon only below sm) + two 12 px gaps + 32 px of padding
// = 314 px (#147): a visitor's has room to spare; the word "Admin" returns at sm, and the gaps at 16 px.
// The command bar (K-07, #585; Spec 07 §3) renders in its slot only while `site.command_bar` is published; until then the slot
// stays empty, as it was, and the mobile search button and ⌘K arrive with it (components/cmdbar/).
import Link from "next/link";
import { CommandBarSlot } from "@/components/cmdbar/CommandBarSlot";
import { AdminBadge } from "@/components/shell/admin";
import { MobileNav } from "@/components/shell/MobileNav";

export function TopBar() {
  return (
    <header className="sticky top-0 z-20 flex h-(--topbar-h) shrink-0 items-center gap-3 border-b border-line bg-panel px-4 sm:gap-4">
      <MobileNav />
      <Link prefetch={false}
        href="/"
        className="flex min-h-6 items-center gap-2 font-mono text-ui font-medium whitespace-nowrap text-ink-1 no-underline lg:min-w-50"
      >
        <span aria-hidden className="size-2 shrink-0 bg-highlight" />
        Finance Terminal
      </Link>
      <CommandBarSlot />
      <div className="ml-auto flex items-center justify-end md:ml-0 lg:min-w-50">
        <AdminBadge />
      </div>
    </header>
  );
}
