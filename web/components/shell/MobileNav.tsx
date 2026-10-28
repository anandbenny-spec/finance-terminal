"use client";

// The drawer below lg (Design 01 §4.1, §5.5): the top bar's Menu button opens the sidebar's links in a sheet, which
// closes when a link is followed, on Esc, or on its close button (focus returns to the Menu button). The button is
// labelled (#147): the menu icon and the word "Menu", an outline button 32 px high with a line-input border. Its
// accessible name is that visible word. The top bar is sticky, so the button stays in view while the page scrolls.
import { Menu } from "lucide-react";
import { Suspense, useState } from "react";
import { NavLinks, NavList } from "@/components/shell/Nav";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="outline" className="shrink-0 border-line-input text-ui lg:hidden">
          <Menu aria-hidden />
          Menu
        </Button>
      </SheetTrigger>
      <SheetContent side="left" closeLabel="Close menu">
        <div className="flex h-(--topbar-h) shrink-0 items-center border-b border-line px-4">
          <SheetTitle>Menu</SheetTitle>
        </div>
        <nav aria-label="Main" className="px-2 py-3">
          <Suspense fallback={<NavList active={null} onNavigate={close} />}>
            <NavLinks onNavigate={close} />
          </Suspense>
        </nav>
      </SheetContent>
    </Sheet>
  );
}
