// /methodology's command bar section (K-12, #589; Spec 07 §1, §4, §6, §7, §10). It renders only while `site.command_bar` is
// published (Spec 07 §3, N1): hidden, it is nothing at all, so the page is unchanged until K-11's judge passes and Anand flips.
// Shown, it says the bar answers only from the data or cited documents, how a data and a document question are answered and
// checked, its limits, the three evaluation gates with their passing results read from `api.eval_summary` (lib/cmdbar.ts
// `gateLines`: no figure is written here), and the judge, named by model as the US slice's is (never Anand: Plan 05 §5).
// The eval read happens only once the bar is shown; it is /accuracy's own cached read (lib/queries/accuracy.ts).
import Link from "next/link";
import { Suspense } from "react";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { Section } from "@/components/data/SectionHeading";
import { StateNote } from "@/components/data/StateNote";
import { gateLines, type GateLine } from "@/lib/cmdbar";
import { readEvalSummary } from "@/lib/queries/accuracy";
import { readCommandBarShown } from "@/lib/queries/cmdbar";

export const SECTION_ID = "cmdbar";

/** The gates' results: lines from the view, or a failed read (the page shows the error state, never the reason). */
export type Gates = { ok: true; lines: GateLine[] } | { ok: false };

function GateResult({ line }: { line: GateLine }) {
  return (
    <li>
      {line.label}:{" "}
      {line.result ? (
        <>
          <span className="font-mono">{line.result.value}</span>, on <span className="font-mono">{line.result.items}</span>{" "}
          questions, run on <span className="font-mono">{line.result.date}</span>.
        </>
      ) : (
        "no passing run is recorded yet."
      )}
    </li>
  );
}

export function MethodologyView({ shown, gates }: { shown: boolean; gates: Gates | null }) {
  if (!shown) return null;
  return (
    <Section id={SECTION_ID} title="The command bar">
      <PanelGrid>
        <Panel title="Questions and answers" titleId={`${SECTION_ID}-title`} variant="columns">
          <div className="space-y-3 text-ink-2">
            <p>
              The command bar at the top of each page searches the site&apos;s meetings, banks, indices and companies, and answers
              questions about them. It answers only from the data or cited documents: from the rows of the site&apos;s published data,
              or from cited passages of the stored Fed and RBI documents, never from general knowledge. A question it can&apos;t answer
              that way is told so, with pages to try, instead of a guess.
            </p>
            <p>
              For a question about the data, Claude Haiku writes a database query over the site&apos;s published views. The query is
              checked before it runs: a single read-only query, over the listed views only, with a row limit. It runs as a database
              user that can read only published data, in a read-only transaction with a short time limit. If the check refuses the
              query or the database rejects it, Claude Sonnet gets one more try. The answer is the rows themselves. A one-line AI
              summary, marked as AI, is shown only when every number in it appears in those rows, and &ldquo;Show SQL&rdquo; shows
              the query that ran. Money in different currencies is never compared, ranked or summed.
            </p>
            <p>
              For a question about the Fed&apos;s or the RBI&apos;s documents, the passages that match it best are found among the
              stored documents, and Claude Haiku answers from those passages with citations. Each citation is checked against the
              stored text, and a sentence without a verified citation is dropped; if none is left, the bar says the answer was not
              found in the stored documents. Earnings-call transcripts are not searched.
            </p>
            <p>
              Each visitor can ask a limited number of AI questions an hour, the bar answers a limited number a day across all
              visitors, and AI spending has a monthly budget. Past a limit, or while AI questions are switched off, the bar says so
              and search still works. What each question logs is on the{" "}
              <Link prefetch={false} className="underline" href="/about#logged">
                About and privacy
              </Link>{" "}
              page.
            </p>
            <p>
              Before the bar was switched on, it had to pass three evaluations, each with its pass rule written down before it was
              run, on questions kept apart from the ones its prompts were written with. A data question&apos;s checked answer is a
              database query written before the evaluation ran. The passing results:
            </p>
            {gates === null || !gates.ok ? (
              <p>
                <StateNote kind="error">Couldn&apos;t load the evaluation results.</StateNote>
              </p>
            ) : (
              <ul className="list-disc space-y-1.5 pl-4.5" data-cmdbar-gates="">
                {gates.lines.map((line) => (
                  <GateResult key={line.key} line={line} />
                ))}
              </ul>
            )}
            <p>
              The bar was then checked by an AI model, Claude Opus, in a separate session that built none of it, against a checklist
              and seeded samples both fixed before the check began: data answers against their checked answers, every number in
              each summary against its rows, document answers&apos; citations, the safety checks and the page. The evaluations&apos;
              results are also on the{" "}
              <Link prefetch={false} className="underline" href="/accuracy">
                Accuracy
              </Link>{" "}
              page.
            </p>
          </div>
        </Panel>
      </PanelGrid>
    </Section>
  );
}

/** The section after the reads: nothing while `site.command_bar` is hidden, and no eval read then either. */
export async function GatedMethodology() {
  if (!(await readCommandBarShown())) return <MethodologyView shown={false} gates={null} />;
  const evals = await readEvalSummary();
  return <MethodologyView shown gates={evals.ok ? { ok: true, lines: gateLines(evals.rows) } : { ok: false }} />;
}

export function CommandBarMethodology() {
  return (
    <Suspense fallback={null}>
      <GatedMethodology />
    </Suspense>
  );
}
