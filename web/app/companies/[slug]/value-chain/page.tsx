// /companies/[slug]/value-chain: the value chain card's sub-page (VC-06, #191; Spec 02B §12.7): the years, the group, the related
// parties, the segments and the peers behind the Overview panel's "Years, group and peers ›" link. Reached only from that link: no
// sidebar entry, not linked from module 1's pages. Every part is gated by its own field policy (all hidden until the hand checks pass
// and Anand flips them), so before the flip the page shows its header and the held notes. An unknown slug answers 404 (checked before
// the Suspense boundary, because a streamed response's status is fixed at 200, as /companies/[slug] does); a failed existence read is
// the error state, never a 404. The year is a search param, so the content renders inside Suspense from cached reads.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { CompanyError } from "@/components/company/CompanyView";
import { ValueChainNotCovered } from "@/components/company/us/ValueChainNotCovered";
import { ValueChainPage } from "@/components/company/value-chain/ValueChainPage";
import { PageLoading } from "@/components/data/PageLoading";
import { readCompanyExists, readHeader } from "@/lib/queries/company";
import { readFreshness } from "@/lib/queries/shell";
import { readValueChainPage } from "@/lib/queries/valuechain";

type Props = PageProps<"/companies/[slug]/value-chain">;

// A route with a dynamic segment needs one slug known when it prerenders; every other company renders on its first request, then is cached.
export function generateStaticParams(): { slug: string }[] {
  return [{ slug: "adanient" }];
}

export const metadata: Metadata = {
  title: "Value chain · Finance Terminal",
  description: "Where a Nifty 50 company buys from, how it sells and how much of that runs through its own group, from its BRSR filings. Informational only, not investment advice.",
};

async function Content({ slug, searchParams }: { slug: string; searchParams: Props["searchParams"] }) {
  const query = await searchParams;
  let header: Awaited<ReturnType<typeof readHeader>>;
  try {
    header = await readHeader(slug);
  } catch (error) {
    console.error(`[value-chain] ${slug} header read failed: ${error instanceof Error ? error.message : String(error)}`);
    return <CompanyError />;
  }
  if (!header.company) return <CompanyError />;
  // A US company has no value chain (US-06, #405): the reason, and no read of the value chain's views.
  if (header.company.market === "US") return <ValueChainNotCovered company={header.company} actions={header.actions} indexNames={header.indexNames} />;
  let data: Awaited<ReturnType<typeof readValueChainPage>>;
  try {
    data = await readValueChainPage(slug);
  } catch (error) {
    console.error(`[value-chain] ${slug} read failed: ${error instanceof Error ? error.message : String(error)}`);
    return <CompanyError />;
  }
  const fresh = await readFreshness();
  return (
    <ValueChainPage
      company={header.company}
      actions={header.actions}
      indexNames={header.indexNames}
      data={data}
      year={query.year}
      freshness={fresh.ok ? fresh.tasks : null}
      now={header.readAt}
    />
  );
}

export default async function Page({ params, searchParams }: Props) {
  const { slug } = await params;
  const exists = await readCompanyExists(slug);
  if (exists === false) notFound();
  if (exists === null) return <CompanyError />;
  return (
    <Suspense fallback={<PageLoading>Loading the value chain…</PageLoading>}>
      <Content slug={slug} searchParams={searchParams} />
    </Suspense>
  );
}
