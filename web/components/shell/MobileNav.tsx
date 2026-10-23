"use client";

// The drawer below lg (Design 01 §4.1, §5.5): the top bar's menu button opens the sidebar's links in a sheet,
// which closes when a link is followed, on Esc, or on its close button (focus returns to the menu button).
import { Menu } from "lucide-react";
import { Suspense, useState } from "react";
import { NavLinks, NavList } from "@/components/shell/Nav";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger className="grid size-8 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-raised hover:text-ink-1 lg:hidden">
        <Menu aria-hidden className="size-4" />
        <span className="sr-only">Open menu</span>
      </SheetTrigger>
      <SheetContent side="left" closeLabel="Close menu">
        <div className="flex h-(--topbar-h) shrink-0 items-center border-b border-line px-4">
          <SheetTitle>Menu</SheetTitle>
        </div>
        <nav aria-label="Main" className="px-2 py-3">
          <Suspense fallback={<NavList active={null} variant="drawer" onNavigate={close} />}>
            <NavLinks variant="drawer" onNavigate={close} />
          </Suspense>
        </nav>
      </SheetContent>
    </Sheet>
  );
}
