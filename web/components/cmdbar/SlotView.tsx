// The top bar's command-bar slot (K-07, #585; Design 01 §4.1, §5.5; Spec 07 §3, N1). Hidden: exactly the empty slot the top bar
// had before the bar was built (centred, at most 640 px, from md), so the top bar looks as it did (the flag-off test,
// components/cmdbar/cmdbar.test.tsx). Shown: the box from md, and below md a search button that opens it as a full-screen sheet.
// Below md the shown slot is a fourth flex item in the top bar, so it takes back the 12 px gap after it (`max-md:-mr-3`): a
// visitor's row at 320 px is then Menu 79 + wordmark 151 + button 32 + two 12 px gaps + 32 px of padding = 318 px (the badge's
// box is empty for a visitor). The admin's row with its badge is wider than 320 px; K-08 (#586) owns the phone layout.
import { CommandBar } from "@/components/cmdbar/CommandBar";

/** The hidden slot's classes, as TopBar had them on `main` before K-07 (pinned by the flag-off test). */
export const HIDDEN_SLOT_CLASS = "mx-auto hidden max-w-160 flex-1 md:block";

export function CommandBarSlotView({ shown }: { shown: boolean }) {
  if (!shown) return <div data-slot="command-bar" className={HIDDEN_SLOT_CLASS} />;
  return (
    <div data-slot="command-bar" className="flex min-w-0 flex-1 justify-end max-md:-mr-3 md:mx-auto md:max-w-160 md:justify-center">
      <CommandBar />
    </div>
  );
}
