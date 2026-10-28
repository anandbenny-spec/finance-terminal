// AppShell (Design 01 §4.1, §5.1, §5.5): top bar, tape, sidebar beside main, status bar. "Skip to content" is
// first in the tab order; the landmarks are header, nav, main and footer. The sidebar is 232 px from lg, its contents
// staying in view while the page scrolls (#147), and a drawer opened from the top bar's Menu button below;
// main keeps a 1280 px column with 24 px gutters (16 below lg).
import type { ReactNode } from "react";
import { AdminProvider } from "@/components/shell/admin";
import { Footer } from "@/components/shell/Footer";
import { Sidebar } from "@/components/shell/Sidebar";
import { StatusBar } from "@/components/shell/StatusBar";
import { TickerTape } from "@/components/shell/TickerTape";
import { TopBar } from "@/components/shell/TopBar";
import { TooltipProvider } from "@/components/ui/tooltip";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <AdminProvider>
      <TooltipProvider>
        <a
          href="#main"
          className="sr-only z-60 rounded-md bg-amber px-2.5 py-1.5 text-amber-ink focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          Skip to content
        </a>
        <div className="flex min-h-dvh flex-col">
          <TopBar />
          <TickerTape />
          <div className="flex flex-1">
            <Sidebar />
            <main id="main" tabIndex={-1} className="min-w-0 flex-1 px-4 pt-5 pb-8 outline-none lg:px-6">
              <div className="mx-auto max-w-7xl">
                {children}
                <Footer />
              </div>
            </main>
          </div>
          <StatusBar />
        </div>
      </TooltipProvider>
    </AdminProvider>
  );
}
