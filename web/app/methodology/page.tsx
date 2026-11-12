// /methodology: "Methodology and limitations" (#201, S6; Plan 04 §4). How the figures and the AI text are made, checked
// and traced, what the site can't do, and who is accountable, written with SEBI's consultation paper on responsible AI/ML
// use in mind (human oversight, accountability, source traceability). Static text: it shows no market figures, so the
// accuracy rule has nothing to check here, and every claim below is one the shipped code backs (the issue's stop rule:
// a claim that can't be checked leaves the page). It describes module 1 (the Fed vs RBI tracker) and, since the 1 Nov
// 2026 extension, module 2's shipped pages (the Nifty 50 screener and company pages) and, since 5 Nov 2026, the earnings-call lines (the calls tab and
// /earnings); and, since 5 Nov 2026, the guidance record's line. RBI is named, never linked (CLAUDE.md §1).
import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { Section } from "@/components/data/SectionHeading";

export const metadata: Metadata = {
  title: "Methodology and limitations · Finance Terminal",
  description:
    "How Finance Terminal's figures and AI-written text are made, checked and traced, what the site cannot do, and who is accountable for it.",
};

type Block = { id: string; title: string; paragraphs: ReactNode[] };

const BLOCKS: Block[] = [
  {
    id: "what",
    title: "What this site is",
    paragraphs: [
      "Finance Terminal is a non-commercial student research project by Anand Benny. It is informational only, not investment advice, and nothing on it is a recommendation to buy or sell anything. It is not a SEBI-registered research analyst or investment adviser.",
      "It reads published central-bank documents, company filings, earnings-call transcripts and market series, and shows what they say. It makes no forecasts of its own and issues no verdicts or recommendations: the screener only sorts the figures it shows, and model-implied values are labelled as such.",
    ],
  },
  {
    id: "data",
    title: "Where the data comes from",
    paragraphs: [
      <>
        The Fed vs RBI policy tracker&apos;s documents come from the Federal Reserve Board and the Reserve Bank of India, its rates
        and exchange rate from the data owners named on each figure, and its index levels from the index owners, each with the source line and copyright
        line its owner asks for. The full list, with the source lines, is on the <Link className="underline" href="/about#sources">About and privacy</Link> page.
      </>,
      "The Nifty 50 screener and company pages read company filings and exchange data. Each company keeps its own reporting currency and market, and figures are never compared, ranked or summed across currencies.",
      "Every piece of data belongs to its owner, who is named on each figure. Market data is end-of-day only. Raw data from any source is never offered for download or export. The browser never calls a data source: it talks only to this site and to its database.",
      "RBI documents are named, with their date and press-release number, and never linked, so they can be found on the RBI's own website.",
    ],
  },
  {
    id: "ai",
    title: "How AI is used",
    paragraphs: [
      "AI writes the short summaries of what changed between central-bank statements and of each earnings call, and extracts structured facts from the documents, such as projections, stance, and the targets and figures management stated on a call. It uses Anthropic's Claude models. The work is done ahead of time in batches, never when a visitor opens a page.",
      "Every AI output is marked as AI. It may only contain numbers that appear in the passage it cites, and a claim without a verified citation into the stored source text is dropped before anything is published. The database itself refuses to publish an AI output that has no citation.",
      "The AI does not answer from general knowledge, and it does not make forecasts, ratings or recommendations. AI text can be incomplete: a missing summary means nothing passed the checks, not that nothing changed.",
    ],
  },
  {
    id: "oversight",
    title: "Human oversight and accountability",
    paragraphs: [
      "A person decides what is shown. Every held-back field is hidden by default: a field with no published policy stays hidden, and it appears only once the reason it was held back is resolved. For an AI-derived score that means its evaluation gate has passed, with the pass rule written down before the evaluation was run.",
      "Wording and design rules that touch accuracy are set by the site's owner, Anand Benny, who is accountable for what is published. AI is a tool inside that process and does not decide what is shown.",
    ],
  },
  {
    id: "switches",
    title: "Kill switches",
    paragraphs: [
      "AI spending has a monthly budget that stops new AI work when it is reached. The daily and watch workflows can also be started with AI switched off, the weekly workflow, which writes the call summaries and guidance, can be switched off as a whole, and when an AI key is missing, refused or out of credit, the AI step is skipped and says so instead of failing the whole update.",
      "Any AI or model field can be hidden at once through its field policy. Any source can be suppressed at once, and if an owner objects to something shown here, the source it comes from is removed promptly.",
    ],
  },
  {
    id: "trace",
    title: "Source traceability",
    paragraphs: [
      "Every figure shows its source line and the time it was last updated. Figures that are derived, approximate, annualised, curated or extracted by AI carry that label next to them. A figure that might be wrong is held back and says so; it is never shown unlabelled.",
      "Every AI claim carries a citation. The quoted passage is rebuilt from the stored source text, not from the model's output, and is never longer than 400 characters. Visitors see those quotes and the documents themselves, never a transcript's full text.",
    ],
  },
  {
    id: "testing",
    title: "Testing and monitoring",
    paragraphs: [
      <>
        Every change runs through automated tests for the code and the database before it is merged. Data loads run blocking checks: a load that fails them is rolled back, the last good data stays, and the site shows “Update delayed” instead of publishing bad numbers. A health check reports the state of the data jobs. The <Link className="underline" href="/accuracy">Accuracy</Link> page publishes how often the site has held something back and how its checks have done, as counts from its own records.
      </>,
    ],
  },
  {
    id: "limits",
    title: "Limitations",
    paragraphs: [
      "The policy tracker covers the Federal Reserve and the Reserve Bank of India only. Market series are end-of-day, so they lag the market, and the India 10-year yield is monthly and says its month.",
      "Vote and stance information is only as complete as the published minutes and statements, which arrive days or weeks after a decision. Until then the site says the minutes are pending rather than guessing.",
      "AI summaries and extractions can miss or misread a passage even when every number in them matches the source. Read the cited passage for anything that matters.",
      "The screener and company pages cover the Nifty 50 only. Valuations shown are model-implied and informational, never a verdict, a target or a recommendation.",
      "Company debt is borrowings only, labelled as excluding lease liabilities because results filings do not report them. Where a company's filed lease payments are material against its operating profit, enterprise-value figures are held back with the reason shown. Operating profit (EBIT) is derived from the filed lines and labelled as derived, and a lender's return on assets is annualised and labelled.",
      "The one-year price return is a price return: it is adjusted for splits and bonus issues but leaves out dividends.",
      "The earnings-calls list shows the latest call of each Nifty 50 company, and a company's Calls tab also lists its earlier calls. Its summary, the guidance given and the figures management stated are written by AI from the stored transcript and labelled AI-extracted, and they are held back until they have passed review, and no tone or sentiment is read from a call.",
      "The guidance record compares each measurable target management gave on a call with the company's own filed figures once they are out: delivered, missed, still pending, or not scored with the reason shown. Only what speakers identified as management said is a target. The tolerance for each kind of target was written down before any target was scored, a target is scored on the first time it was stated, and a revised target is shown as revised. A missed target is a fact about a figure, not a verdict on management. The record is held back until it has passed review, and the share delivered appears only once a company has at least five scored targets.",
      "Some calls are not analysed: a transcript that is only a scan, or one kept only on the company's own website that the site has no stored copy of, is named as such and not summarised. What speakers whose role could not be read said is shown apart and is not attributed to management.",
      "Transcript text stays private. Visitors see only the short quotes of verified citations, each with its page where the page could be matched, never a transcript itself.",
      "Where a company's value chain card is shown, its figures are the ones the company tagged in the BRSR's XBRL file filed with the exchange. The company's PDF of the same report may round a figure differently, or say \"Not applicable\" where the XBRL file carries zero, and a restatement is shown only where the next year's XBRL file carries the earlier year's figure.",
      "The site makes no claim about future rates or prices.",
    ],
  },
  {
    id: "contact",
    title: "Accountability and contact",
    paragraphs: [
      <>
        The site is run by Anand Benny. If you own data shown here and want it removed, say so to whoever shared this link with you and it will reach him; it
        is removed promptly. The site collects no personal data beyond what the <Link className="underline" href="/about#logged">About and privacy</Link> page describes.
      </>,
    ],
  },
];

export default function MethodologyPage() {
  return (
    <>
      <PageHeader
        crumbs={["About", "Methodology and limitations"]}
        title="Methodology and limitations"
        description="How the figures and the AI-written text are made, checked and traced, and what this site cannot do."
      />

      <Section id="alignment" title="Alignment note">
        <PanelGrid>
          <Panel title="Responsible AI use" titleId="alignment-title" variant="columns">
            <p className="text-ink-1">
              Written with SEBI&apos;s consultation paper &ldquo;Guidelines for Responsible Usage of AI/ML in Indian Securities Markets&rdquo; (20 June
              2025) in mind, and the final guidelines SEBI said in August 2026 were coming, which it said would stress human oversight, data controls and kill switches. This site is not a
              regulated entity and claims no compliance or endorsement.
            </p>
          </Panel>
        </PanelGrid>
      </Section>

      {BLOCKS.map((b) => (
        <Section key={b.id} id={b.id} title={b.title}>
          <PanelGrid>
            <Panel title={b.title} titleId={`${b.id}-title`} variant="columns">
              <div className="space-y-3 text-ink-2">
                {b.paragraphs.map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </div>
            </Panel>
          </PanelGrid>
        </Section>
      ))}
    </>
  );
}
