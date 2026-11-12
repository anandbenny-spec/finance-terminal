// /companies/[slug]: one page per company (2A-19; Spec 02A §8). A header (identifiers, peer group, index chips, last
// close), then an Overview tab (price chart, key figures, peers); the other tabs are /companies/[slug]/[...path]
// (financials, valuation, rate-decisions, calls, each with an optional view). Every figure a row an `api` view returned
// (lib/queries/company.ts), formatted by lib/format.ts. Informational only: no verdict wording (Brief §11). Not in the
// sidebar and not linked from module 1's pages: 2A-26 adds the links after its checks, so the shared link doesn't
// advertise a page still being built; the route works for review (the screener's rows already link here).
//
// The page reads no query string (R05-9b, 12 Nov 2026), so it is built ahead and cached; see components/company/CompanyRoute.tsx.
import type { Metadata } from "next";
import { companyMetadata, renderCompany } from "@/components/company/CompanyRoute";
import { DEFAULT_VIEW } from "@/lib/equity/company";

type Props = PageProps<"/companies/[slug]">;

// A route with a dynamic segment needs one slug known when it prerenders; the first of the Nifty 50's slugs (curated in
// py/config/equity/companies.yaml). Every other company renders on its first request, then is cached.
export function generateStaticParams(): { slug: string }[] {
  return [{ slug: "adanient" }];
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return companyMetadata((await params).slug);
}

export default async function Page({ params }: Props) {
  const { slug } = await params;
  return renderCompany(slug, "overview", DEFAULT_VIEW);
}
