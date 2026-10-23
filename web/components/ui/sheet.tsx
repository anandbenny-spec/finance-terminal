"use client"

// Sheet (a dialog that slides in from an edge), on Radix's Dialog (radix-ui), with shadcn's component API (Sheet,
// SheetTrigger, SheetClose, SheetContent, SheetTitle). Written by hand in C2: Claude's shells can't reach
// ui.shadcn.com, so `shadcn add sheet` can't run there (web/README.md). Restyled by the tokens only. Layer 50
// (Design 01 §2.5); the page behind is dimmed with the canvas colour. Radix traps focus inside, closes on Esc and
// returns focus to the trigger.
import * as React from "react"
import { cn } from "cn"
import { X } from "lucide-react"
import { Dialog as SheetPrimitive } from "radix-ui"

function Sheet(props: React.ComponentProps<typeof SheetPrimitive.Root>) {
  return <SheetPrimitive.Root data-slot="sheet" {...props} />
}

function SheetTrigger(props: React.ComponentProps<typeof SheetPrimitive.Trigger>) {
  return <SheetPrimitive.Trigger data-slot="sheet-trigger" {...props} />
}

function SheetClose(props: React.ComponentProps<typeof SheetPrimitive.Close>) {
  return <SheetPrimitive.Close data-slot="sheet-close" {...props} />
}

function SheetTitle({ className, ...props }: React.ComponentProps<typeof SheetPrimitive.Title>) {
  return <SheetPrimitive.Title data-slot="sheet-title" className={cn("text-data font-semibold text-ink-1", className)} {...props} />
}

function SheetContent({
  className,
  children,
  side = "left",
  closeLabel = "Close",
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Content> & { side?: "left" | "right"; closeLabel?: string }) {
  return (
    <SheetPrimitive.Portal>
      <SheetPrimitive.Overlay data-slot="sheet-overlay" className="fixed inset-0 z-50 bg-canvas/80" />
      <SheetPrimitive.Content
        data-slot="sheet-content"
        aria-describedby={undefined}
        className={cn(
          "fixed inset-y-0 z-50 flex w-(--sidebar-w) max-w-full flex-col overflow-y-auto border-line-strong bg-panel outline-none",
          side === "left" ? "left-0 border-r" : "right-0 border-l",
          className
        )}
        {...props}
      >
        {children}
        <SheetPrimitive.Close
          data-slot="sheet-close"
          className="absolute top-2.5 right-2.5 grid size-8 place-items-center rounded-md text-ink-2 hover:bg-raised hover:text-ink-1"
        >
          <X className="size-4" aria-hidden />
          <span className="sr-only">{closeLabel}</span>
        </SheetPrimitive.Close>
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  )
}

export { Sheet, SheetClose, SheetContent, SheetTitle, SheetTrigger }
