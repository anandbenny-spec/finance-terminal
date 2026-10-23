"use client"

// Tooltip, on Radix (radix-ui), with shadcn's component API (TooltipProvider, Tooltip, TooltipTrigger,
// TooltipContent). Written by hand in C2: Claude's shells can't reach ui.shadcn.com, so `shadcn add tooltip` can't
// run there (web/README.md). Restyled by the tokens only: a raised surface with the strong outline, as Design 01
// §2.5 sets popovers (layer 40), and no shadow, since the token file has no shadow colour.
import * as React from "react"
import { cn } from "cn"
import { Tooltip as TooltipPrimitive } from "radix-ui"

function TooltipProvider({ delayDuration = 200, ...props }: React.ComponentProps<typeof TooltipPrimitive.Provider>) {
  return <TooltipPrimitive.Provider data-slot="tooltip-provider" delayDuration={delayDuration} {...props} />
}

function Tooltip(props: React.ComponentProps<typeof TooltipPrimitive.Root>) {
  return <TooltipPrimitive.Root data-slot="tooltip" {...props} />
}

function TooltipTrigger(props: React.ComponentProps<typeof TooltipPrimitive.Trigger>) {
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />
}

function TooltipContent({
  className,
  sideOffset = 6,
  collisionPadding = 8,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        className={cn(
          "z-40 max-w-90 rounded-md border border-line-strong bg-raised px-2.5 py-1.5 font-sans text-meta text-ink-1",
          className
        )}
        {...props}
      />
    </TooltipPrimitive.Portal>
  )
}

export { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger }
