// /about: "About and privacy" (#86; Brief §10, §6.2; CLAUDE.md §1). It opens with the data ownership statement
// (decided by Anand, 22 Oct 2026), then says what is logged today (only what is true for this link: the command bar
// isn't live, so no visitor questions are stored), the sources module 1 uses with their source lines, and how an
// owner's objection is handled. Static text: it shows no figures, so the accuracy rule has nothing to check here.
// RBI is named, never linked (CLAUDE.md §1). Update the "What is logged" section when the command bar lands.
import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { Section } from "@/components/data/SectionHeading";

export const metadata: Metadata = {
  title: "About and privacy · Finance Terminal",
  description:
    "Every piece of data on Finance Terminal belongs to its owner, named on each figure. The project is non-commercial. What is logged, and the sources behind the Fed vs RBI tracker.",
};

type Source = { name: string; line: string; used: ReactNode };

// The source lines are the ones the figures carry (core.source and the series' own attribution).
const SOURCES: Source[] = [
  {
    name: "Federal Reserve Board",
    line: "Source: Federal Reserve Board",
    used: "FOMC statements, minutes, press-conference transcripts and the meeting calendar. The Board's content is in the public domain and asks to be cited as the source.",
  },
  {
    name: "Reserve Bank of India",
    line: "Source: Reserve Bank of India",
    used: "MPC resolutions, minutes and the Governor's statements. © Reserve Bank of India. Each RBI figure names the document, its date and RBI's press-release number, so it can be found on RBI's own website; this site does not link to RBI's pages.",
  },
  {
    name: "U.S. Treasury, the Federal Reserve Board and OECD, via FRED",
    line: "Source: U.S. Treasury, via Federal Reserve Board (H.15) and FRED · Source: OECD, via FRED · Source: Federal Reserve Board (H.10), via FRED",
    used: "The US 10-year yield, the India 10-year government bond yield (monthly, so it says its month) and the USD/INR rate, read through FRED, the Federal Reserve Bank of St. Louis's data service.",
  },
  {
    name: "Index levels, via Yahoo Finance",
    line: "Source: NSE Indices, via Yahoo Finance · Source: BSE Index Services, via Yahoo Finance · Source: S&P Dow Jones Indices LLC, via Yahoo Finance · Source: Nasdaq, Inc., via Yahoo Finance",
    used: "End-of-day levels of the Nifty 50 and Sensex (owned by NSE Indices and BSE Index Services), the S&P 500 and Dow Jones Industrial Average (© S&P Dow Jones Indices LLC), and the Nasdaq Composite and Nasdaq-100 (© Nasdaq, Inc.). End-of-day only, each with its date.",
  },
  {
    name: "Curated by Finance Terminal",
    line: "Curated",
    used: "A few facts no official feed publishes in a usable form, such as the usual clock times of decisions and the committee rosters. They are labelled curated.",
  },
];

export default function AboutPage() {
  return (
    <>
      <PageHeader
        crumbs={["About", "About and privacy"]}
        title="About and privacy"
        description="Who owns the data, what this site logs, and where the Fed vs RBI tracker gets its figures."
      />

      <Section id="ownership" title="Who owns the data">
        <PanelGrid>
          <Panel title="Data ownership" titleId="ownership-title" variant="prose">
            <div className="space-y-3 text-ink-1">
              <p>
                Every piece of data on this site belongs to its owner, and the owner is named on each figure. This is a non-commercial
                project: it earns no money, and it sells, licenses or exports no data.
              </p>
              <p className="text-ink-2">
                That is a promise of no monetary gain and no data resale, not a claim that nothing here is shown, and it does not replace
                permission. If you own data shown on this site and want it removed, it is removed.
              </p>
              <p className="text-ink-2">
                The site is a student project by Anand Benny. It is informational only, not investment advice: nothing here is a
                recommendation to buy or sell anything.
              </p>
            </div>
          </Panel>
        </PanelGrid>
      </Section>

      <Section id="logged" title="What is logged">
        <PanelGrid>
          <Panel title="Visitors" titleId="logged-title" variant="prose">
            <div className="space-y-3 text-ink-2">
              <p>
                This site sets no cookies for visitors, has no visitor accounts and runs no analytics or advertising trackers. Only the
                site&apos;s administrator logs in.
              </p>
              <p>
                The command bar is not live on this link, so no questions from visitors are collected or stored. This note is updated when
                it goes live, because its logging will change what is stored.
              </p>
              <p>
                The figures are read straight from the project&apos;s database, so your browser talks to the site and to its database
                provider, Supabase. The site is hosted on Vercel. Like any web host, Vercel and Supabase may keep standard technical
                request logs (such as times and network addresses) to run and secure their services. This project does not use them
                for analytics.
              </p>
            </div>
          </Panel>
        </PanelGrid>
      </Section>

      <Section id="sources" title="Where the figures come from" note="Fed vs RBI policy tracker">
        <PanelGrid>
          <Panel title="Sources" titleId="sources-title" variant="prose">
            <p className="mb-3 text-ink-2">
              Every figure shows its source line and the time it was last updated. These are the sources behind this link, with the
              source lines they appear under.
            </p>
            <ul className="space-y-4">
              {SOURCES.map((s) => (
                <li key={s.name}>
                  <h4 className="font-semibold text-ink-1">{s.name}</h4>
                  <p className="text-ink-2">{s.used}</p>
                  <p className="mt-1 text-meta text-ink-3">{s.line}</p>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="AI-written text" titleId="ai-title" variant="prose">
            <p className="text-ink-2">
              Summaries and other AI-written text are marked as AI and cite the passage of a stored document they come from. Text without
              a verified citation is not published.{" "}
              <Link href="/methodology" className="underline">
                How the figures and AI text are made and checked
              </Link>
              .
            </p>
          </Panel>
        </PanelGrid>
      </Section>

      <Section id="objections" title="If you own data shown here">
        <PanelGrid>
          <Panel title="Objections" titleId="objections-title" variant="prose">
            <p className="text-ink-2">
              If an owner objects to anything on this site, it is removed promptly. Tell whoever shared this link with you, and it
              will reach the person who runs the site.
            </p>
          </Panel>
        </PanelGrid>
      </Section>
    </>
  );
}
