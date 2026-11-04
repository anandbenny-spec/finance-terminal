// The document page (C5; Spec 01 §4.4; Design 01 §4.5, §5.6), for /documents/[id]: the target of every "Open in
// document" link. Header: breadcrumb (the meeting it belongs to), the title, the bank, the type and the date, and the
// official link (the Fed's only: the RBI's documents are named, never linked: Anand, 24 Oct 2026). Then the AI
// summary, collapsed, above the text, and the text in the reading layout with ¶ anchors (DocumentView). A cited span
// (?span=start-end, code points) is wrapped in <mark> with "Cited by: AI summary, point 2" above it, but only when a
// published, verified citation points at exactly that span (lib/policy/document.ts). Every word of the text is a
// chunk body the database stored; the panel carries SourceLine and Freshness; nothing is ever blank (StateNote).
import { Sparkles } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { AIBlock } from "@/components/ai/AIBlock";
import { DocumentView } from "@/components/ai/DocumentView";
import { Freshness } from "@/components/data/Freshness";
import { Gated, fieldPolicy } from "@/components/data/Gated";
import { PageHeader, type Crumb } from "@/components/data/PageHeader";
import { Panel } from "@/components/data/Panel";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { FEEDS, PREVIEW_FEEDS } from "@/lib/feeds";
import { dateInZone, formatDate } from "@/lib/format";
import { summaryBlock } from "@/lib/policy/ai";
import { BANK_NAME, bankOf, citedBy, documentDate, documentSource, documentTitle, meetingOf, officialUrl, type CitedBy } from "@/lib/policy/document";
import { paragraphs, textFromChunks, type Paragraph } from "@/lib/policy/text";
import type { DocumentRow, TaskFreshnessRow } from "@/lib/queries/api";
import { readDocumentPage, readSpanCitations, type DocumentPage as PageData } from "@/lib/queries/document";
import type { Bank } from "@/lib/queries/policy";
import { readFreshness } from "@/lib/queries/shell";
import { panelFreshness } from "@/lib/shell/freshness";

const ERROR = "Couldn't load this panel. Try again.";

function OfficialLink({ doc, bank }: { doc: DocumentRow; bank: Bank }) {
  const url = officialUrl(doc, bank);
  if (!url) return null;
  return (
    <a href={url} className="text-meta text-ink-1 underline" rel="noreferrer" target="_blank">
      Official source ↗
    </a>
  );
}

/** The document's AI summary, collapsed (a disclosure), or the one line saying why there is none. */
function Summary({ page, bank }: { page: PageData; bank: Bank }) {
  if (!page.summary.ok || !page.fieldPolicy.ok) return <StateNote kind="error">Couldn&apos;t load the AI summary.</StateNote>;
  const policies = page.fieldPolicy.rows;
  const policy = fieldPolicy(policies, "ai.doc_summary");
  if (!policy || policy.status === "hidden") return null; // fail closed: no row, no summary, no line
  const s = page.summary.cited;
  const block = s ? summaryBlock(s.output, s.citations, { linkOfficial: bank === "Fed", label: "AI summary" }) : null;
  if (!block) return <StateNote kind="unavailable">No AI summary of this document yet.</StateNote>;
  const points = block.bullets.length === 1 ? "1 point" : `${block.bullets.length} points`;
  return (
    <details className="group rounded-md border border-line bg-raised px-3 py-2">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-data font-medium text-ink-1 [&::-webkit-details-marker]:hidden">
        <Sparkles aria-hidden className="size-3.5 text-highlight" />
        <span className="group-open:hidden">Show the AI summary</span>
        <span className="hidden group-open:inline">Hide the AI summary</span>
        <span className="font-normal text-ink-3">· {points}</span>
      </summary>
      <div className="mt-2.5">
        <Gated policies={policies} field="ai.doc_summary" showLabel={false}>
          <AIBlock block={block} now={page.readAt} dense={false} />
        </Gated>
      </div>
    </details>
  );
}

/** Document types whose text is never shown (core.doc_type.text_public = false: Spec 02B §3.2; CLAUDE.md §1). */
const PRIVATE_TEXT = new Set(["call_transcript", "call_cover_letter"]);

export const isPrivateText = (doc: Pick<DocumentRow, "doc_type">): boolean => PRIVATE_TEXT.has(doc.doc_type);

export const PRIVATE_TEXT_NOTE = "This transcript isn't reproduced here. Read it at the source.";

/**
 * A transcript's page (2B-19; Spec 02B §8): its metadata and links, and one line saying why there is no text. The text is
 * never read here (the chunks of a private document are invisible to visitors) and never shown; the only quotes anywhere are
 * a verified citation's (CitationChip). The official link is the exchange's own file (or the company's); never our copy.
 */
function PrivateTextPage({ doc, readAt, freshness }: { doc: DocumentRow; readAt: string; freshness: TaskFreshnessRow[] | null }) {
  const date = doc.published_at ? dateInZone(doc.published_at, "Asia/Kolkata") : null;
  const official = doc.source_url?.startsWith("https://") ? doc.source_url : null;
  const callHref = `/companies/${doc.entity_slug}?tab=calls${doc.event_slug ? `&call=${doc.event_slug}` : ""}`;
  const fresh = panelFreshness(freshness, PREVIEW_FEEDS.calls, date, readAt);
  const source = doc.attribution ?? "Source: NSE";
  const link = (
    <>
      {official && (
        <a href={official} className="text-meta text-ink-1 underline" rel="noreferrer" target="_blank">
          Official source ↗
        </a>
      )}
    </>
  );
  return (
    <>
      <PageHeader
        crumbs={["Company research", { label: "Company", href: `/companies/${doc.entity_slug}` }, doc.doc_type_label]}
        title={documentTitle(doc)}
        description={[doc.doc_type_label, date ? formatDate(date) : null].filter(Boolean).join(" · ")}
        meta={
          <>
            <span>{source}</span>
            {doc.version_no > 1 && <span>Version {doc.version_no}</span>}
            {link}
          </>
        }
      />
      <div className="mt-5">
        <Panel
          title={doc.doc_type_label}
          titleId="panel-document"
          subtitle="Where can this transcript be read?"
          actions={link}
          footer={
            <>
              <SourceLine lines={[source]} />
              <Freshness freshness={fresh} />
            </>
          }
        >
          <div className="flex flex-col gap-3">
            <StateNote kind="unavailable">{PRIVATE_TEXT_NOTE}</StateNote>
            <p className="text-data">
              <Link href={callHref} className="text-ink-1 underline">
                See this call&apos;s AI summary and figures
              </Link>
            </p>
          </div>
        </Panel>
      </div>
    </>
  );
}

function CitedByNote({ entries }: { entries: CitedBy[] }) {
  return (
    <p className="text-meta text-ink-2" id="cited-by">
      Cited by:{" "}
      {entries.map((e, i) => (
        <span key={`${e.text}-${i}`}>
          {i > 0 && "; "}
          {e.href ? (
            <a href={e.href} className="text-ink-1 underline">
              {e.text}
            </a>
          ) : (
            e.text
          )}
        </span>
      ))}
    </p>
  );
}

function textOf(page: PageData, id: number): Paragraph[] | null {
  if (!page.chunks.ok || page.chunks.rows.length === 0) return null;
  try {
    return paragraphs(textFromChunks(page.chunks.rows));
  } catch (error) {
    console.error(`[document] ${id}: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

export async function DocumentPage({ id, span }: { id: number; span: [number, number] | null }) {
  const [page, freshRead, spanRead] = await Promise.all([
    readDocumentPage(id),
    readFreshness(),
    span ? readSpanCitations(id, span[0], span[1]) : Promise.resolve(null),
  ]);
  if (!page.document.ok) {
    return (
      <Panel title="Document" titleId="panel-document">
        <StateNote kind="error">{ERROR}</StateNote>
      </Panel>
    );
  }
  const doc = page.document.row;
  if (doc && isPrivateText(doc)) return <PrivateTextPage doc={doc} readAt={page.readAt} freshness={freshRead.ok ? freshRead.tasks : null} />;
  const bank = doc ? bankOf(doc) : null;
  if (!doc || !bank) notFound();

  const date = documentDate(doc, bank);
  const meeting = meetingOf(doc.event_slug);
  const paras = textOf(page, id);
  // Highlight only a span a published citation points at, inside the text we hold.
  const length = paras?.at(-1)?.end ?? 0;
  const entries = span && spanRead?.ok && span[1] <= length ? citedBy(id, span, spanRead.citations, spanRead.outputs) : [];
  const mark = span && entries.length > 0 ? span : null;
  const tasks = bank === "Fed" ? FEEDS.meetingFed : FEEDS.meetingRbi;
  const freshness = panelFreshness(freshRead.ok ? freshRead.tasks : null, tasks, date, page.readAt);

  const crumbs: Crumb[] = ["Macro and policy", { label: "Fed vs RBI", href: "/policy" }];
  if (meeting) crumbs.push({ label: meeting.label, href: meeting.href });
  crumbs.push(doc.doc_type_label);
  const description: ReactNode = [BANK_NAME[bank], doc.doc_type_label, date ? formatDate(date) : null].filter(Boolean).join(" · ");

  return (
    <>
      <PageHeader
        crumbs={crumbs}
        title={documentTitle(doc)}
        description={description}
        meta={
          <>
            <span>{documentSource(doc, bank)}</span>
            {doc.version_no > 1 && <span>Version {doc.version_no}</span>}
            <OfficialLink doc={doc} bank={bank} />
          </>
        }
      />
      <div className="mt-5">
        <Panel
          title={doc.doc_type_label}
          titleId="panel-document"
          subtitle="What does the document say, word for word?"
          market={bank === "Fed" ? "US" : "IN"}
          actions={<OfficialLink doc={doc} bank={bank} />}
          refresh={tasks}
          variant="prose"
          footer={
            <>
              <SourceLine lines={[documentSource(doc, bank)]} />
              <Freshness freshness={freshness} />
            </>
          }
        >
          {!paras ? (
            <StateNote kind="error">{ERROR}</StateNote>
          ) : (
            <div className="flex flex-col gap-5">
              <Summary page={page} bank={bank} />
              <div className="[&_mark]:scroll-mt-24">
                <DocumentView paragraphs={paras} mark={mark} markNote={mark ? <CitedByNote entries={entries} /> : undefined} label={documentTitle(doc)} />
              </div>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
