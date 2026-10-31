// /companies/[slug]: one page per company (2A-19; Spec 02A §8). A header (identifiers, peer group, index chips, last
// close), then an Overview tab (price chart, key figures, peers) and a Financials tab (statements, bank ratios, promoter
// holding, corporate actions), every figure a row an `api` view returned (lib/queries/company.ts), formatted by
// lib/format.ts. Informational only: no verdict wording (Brief §11). Not in the sidebar and not linked from module 1's
// pages: 2A-26 adds the links after its checks, so the shared link doesn't advertise a page still being built; the route
// works for review (the screener's rows already link here).
//
// An unknown slug answers 404: the page checks BEFORE its Suspense boundary, because once a response streams its status
// is fixed at 200 (as /policy/fed/[date] does). A failed existence read is the error state, never a 404. The tab and
// basis are search params, known only on request, so the tab's content renders inside Suspense from cached reads.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { CompanyError, CompanyView } from "@/components/company/CompanyView";
import { StateNote } from "@/components/data/StateNote";
import { basisOf, tabOf } from "@/lib/equity/company";
import { readCompanyExists, readCompanyPage } from "@/lib/queries/company";
import { readFreshness } from "@/lib/queries/shell";

type Props = PageProps<"/companies/[slug]">;

// A route with a dynamic segment needs one slug known when it prerenders; the first of the Nifty 50's slugs (curated in
// py/config/equity/companies.yaml). Every other company renders on its first request, then is cached.
export function generateStaticParams(): { slug: string }[] {
  return [{ slug: "adanient" }];
}

export const metadata: Metadata = {
  title: "Company · Finance Terminal",
  description: "A Nifty 50 company's price, key figures, peers, filed financials and corporate actions. Informational only, not investment advice.",
};

async function Company({ slug, searchParams }: { slug: string; searchParams: Props["searchParams"] }) {
  const query = await searchParams;
  const [data, fresh] = await Promise.all([readCompanyPage(slug, tabOf(query.tab)), readFreshness()]);
  if (!data.ok) return <CompanyError />;
  return <CompanyView data={data} basis={basisOf(query.basis)} freshness={fresh.ok ? fresh.tasks : null} />;
}

export default async function Page({ params, searchParams }: Props) {
  const { slug } = await params;
  const exists = await readCompanyExists(slug);
  if (exists === false) notFound();
  if (exists === null) return <CompanyError />;
  return (
    <Suspense fallback={<StateNote kind="empty">Loading the company…</StateNote>}>
      <Company slug={slug} searchParams={searchParams} />
    </Suspense>
  );
}
