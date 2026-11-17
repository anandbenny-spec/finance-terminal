// About's "What is logged" for the command bar (K-12, #589; Spec 07 §7, §8, N7; Architecture 03 §1.8). It renders only while
// `site.command_bar` is published (Spec 07 §3, N1). Hidden, it is exactly the paragraph the About page had before (the bar
// "not live on this link"), so the page is unchanged until K-11's judge passes and Anand flips. Shown, it names every
// `ops.cmdbar_log` column (lib/cmdbar.ts `LOGGED`, checked against the migrations by components/cmdbar/pages.test.tsx), how
// long rows are kept (`cmdbar.log_retention`: 90 days), what is never kept (no IP address, user agent or cookie: N7), and
// where a question goes to be answered. The read is the box's own (lib/queries/cmdbar.ts), cached like the shell's; while it is
// pending, and in a test's static render, the hidden paragraph shows.
import { Suspense } from "react";
import { LOGGED } from "@/lib/cmdbar";
import { readCommandBarShown } from "@/lib/queries/cmdbar";

/** The About page's paragraph before the bar is published, word for word as it was on `main` before K-12. */
export const NOT_LIVE =
  "The command bar is not live on this link, so no questions from visitors are collected or stored. This note is updated when it goes live, because its logging will change what is stored.";

export function LoggedView({ shown }: { shown: boolean }) {
  if (!shown) return <p>{NOT_LIVE}</p>;
  return (
    <>
      <p>
        Questions asked in the command bar are logged, to keep the bar within its limits and to check its answers. For each
        question, the log keeps:
      </p>
      <ul className="list-disc space-y-1.5 pl-4.5" data-cmdbar-logged="">
        {LOGGED.map((line) => (
          <li key={line.columns[0]}>{line.text}</li>
        ))}
      </ul>
      <p>
        Each entry is kept for 90 days and then deleted. The log holds no IP address, no browser details and no cookie, and asking
        a question needs no account.
      </p>
      <p>
        To answer a question, the bar sends it to Anthropic&apos;s Claude models and, for a question about the Fed and RBI documents,
        to Voyage AI to find the matching passages. A question answered from a stored answer, or refused at a limit, goes to neither.
      </p>
    </>
  );
}

/** The paragraph after the read: the bar's log only when `site.command_bar` is shown or labelled. */
export async function GatedLogged() {
  return <LoggedView shown={await readCommandBarShown()} />;
}

export function CommandBarLogged() {
  return (
    <Suspense fallback={<LoggedView shown={false} />}>
      <GatedLogged />
    </Suspense>
  );
}
