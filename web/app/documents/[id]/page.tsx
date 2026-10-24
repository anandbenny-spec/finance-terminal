// /documents/[id]: a document in the reading layout (C5; Spec 01 §4.4), the target of every citation's "Open in
// document" link (/documents/12?span=340-512#cited). The id and the span are parsed strictly; a malformed id is a
// 404, a malformed span highlights nothing. Rendered on request inside Suspense (the params are only known then),
// from cached reads (lib/queries/document.ts).
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { StateNote } from "@/components/data/StateNote";
import { DocumentPage } from "@/components/policy/DocumentPage";
import { documentTitle, parseDocumentId, parseSpan } from "@/lib/policy/document";
import { readDocumentPage } from "@/lib/queries/document";

type Props = PageProps<"/documents/[id]">;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const id = parseDocumentId((await params).id);
  if (id === null) return { title: "Not found · Finance Terminal" };
  const page = await readDocumentPage(id);
  const title = page.document.ok && page.document.row ? documentTitle(page.document.row) : "Document";
  return { title: `${title} · Finance Terminal` };
}

async function Document({ params, searchParams }: Props) {
  const [{ id: raw }, query] = await Promise.all([params, searchParams]);
  const id = parseDocumentId(raw);
  if (id === null) notFound();
  return <DocumentPage id={id} span={parseSpan(query.span)} />;
}

export default function Page(props: Props) {
  return (
    <Suspense fallback={<StateNote kind="empty">Loading the document…</StateNote>}>
      <Document {...props} />
    </Suspense>
  );
}
