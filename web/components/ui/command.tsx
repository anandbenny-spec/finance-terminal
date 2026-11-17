"use client"

// Command, on cmdk 1.1.1 (Design 01 §1.4, §4.6, §8.4), with shadcn's component API (Command, CommandInput, CommandList,
// CommandEmpty, CommandGroup, CommandItem, CommandSeparator). Written by hand in K-07 (#585), like C2's tooltip and sheet and
// C3's popover: Claude's shells can't reach ui.shadcn.com (web/README.md). Restyled by the tokens only. The command bar
// filters on the server (`shouldFilter={false}`), so cmdk here gives the combobox's keyboard model: arrows move, Enter
// selects, the input stays focused, and the highlighted row is read out (`aria-activedescendant`). No CommandDialog: the
// box opens in its own popover (desktop) or sheet (mobile).
import * as React from "react"
import { Command as CommandPrimitive } from "cmdk"
import { cn } from "cn"

function Command({ className, ...props }: React.ComponentProps<typeof CommandPrimitive>) {
  return <CommandPrimitive data-slot="command" className={cn("flex w-full flex-col text-ink-1", className)} {...props} />
}

function CommandInput({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Input>) {
  return (
    <CommandPrimitive.Input
      data-slot="command-input"
      className={cn(
        "h-full min-w-0 flex-1 bg-transparent text-ui text-ink-1 outline-none placeholder:text-ink-3 disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
}

function CommandList({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.List>) {
  return <CommandPrimitive.List data-slot="command-list" className={cn("overflow-x-hidden overflow-y-auto", className)} {...props} />
}

function CommandEmpty(props: React.ComponentProps<typeof CommandPrimitive.Empty>) {
  return <CommandPrimitive.Empty data-slot="command-empty" className="px-3 py-2 text-ui text-ink-2" {...props} />
}

function CommandGroup({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Group>) {
  return (
    <CommandPrimitive.Group
      data-slot="command-group"
      className={cn(
        "px-1 py-1 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-meta [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-ink-3",
        className
      )}
      {...props}
    />
  )
}

function CommandItem({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        "flex min-h-8 cursor-pointer items-center gap-2 rounded-sm px-2 text-ui text-ink-1 outline-none select-none data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50 data-[selected=true]:bg-active",
        className
      )}
      {...props}
    />
  )
}

function CommandSeparator({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Separator>) {
  return <CommandPrimitive.Separator data-slot="command-separator" className={cn("my-1 h-px bg-line", className)} {...props} />
}

export { Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem, CommandSeparator }
