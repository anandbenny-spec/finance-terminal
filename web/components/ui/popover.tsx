"use client"

// Popover, on Radix (radix-ui), with shadcn's component API (Popover, PopoverTrigger, PopoverContent). Written by
// hand in C3, like C2's tooltip and sheet: Claude's shells can't reach ui.shadcn.com (web/README.md). Restyled by
// the tokens only: a raised surface, the strong outline, layer 40 (Design 01 §2.5). Radix moves focus into it,
// closes on Esc and outside clicks, and returns focus to the trigger.
import * as React from "react"
import { cn } from "cn"
import { Popover as PopoverPrimitive } from "radix-ui"

function Popover(props: React.ComponentProps<typeof PopoverPrimitive.Root>) {
  return <PopoverPrimitive.Root data-slot="popover" {...props} />
}

function PopoverTrigger(props: React.ComponentProps<typeof PopoverPrimitive.Trigger>) {
  return <PopoverPrimitive.Trigger data-slot="popover-trigger" {...props} />
}

function PopoverContent({
  className,
  sideOffset = 6,
  collisionPadding = 8,
  ...props
}: React.ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        data-slot="popover-content"
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        className={cn(
          "z-40 w-90 max-w-(--radix-popover-content-available-width) rounded-md border border-line-strong bg-raised p-3 text-data text-ink-1 outline-none",
          className
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  )
}

export { Popover, PopoverContent, PopoverTrigger }
