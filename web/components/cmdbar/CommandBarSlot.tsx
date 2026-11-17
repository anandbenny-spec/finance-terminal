// The command bar in TopBar's slot, behind its field key (K-07, #585; Spec 07 §3, N1): `site.command_bar` has no
// core.field_policy row until K-11's judge passes and Anand flips, so the slot stays as it was. TopBar renders this one line; the
// read is cached like the shell's (lib/queries/cmdbar.ts), so it is part of the prerendered shell. The fallback is the hidden
// slot itself: while the read is pending, and wherever it can't run (a test's static render), the top bar is unchanged.
import { Suspense } from "react";
import { CommandBarSlotView } from "@/components/cmdbar/SlotView";
import { readCommandBarShown } from "@/lib/queries/cmdbar";

/** The slot after the read: the box only when the key is shown or labelled. */
export async function GatedSlot() {
  return <CommandBarSlotView shown={await readCommandBarShown()} />;
}

export function CommandBarSlot() {
  return (
    <Suspense fallback={<CommandBarSlotView shown={false} />}>
      <GatedSlot />
    </Suspense>
  );
}
