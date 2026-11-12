// /companies/[slug]/financials, /valuation, /rate-decisions and /calls, each with an optional view: /financials/standalone,
// /rate-decisions/d5, /calls/q2-fy27 (R05-9b, 12 Nov 2026; lib/equity/company.ts `pathParam`). The tab and its view are path
// segments, not query strings, so every one is built ahead and cached like Policy Day. Any other path answers 404: an unknown
// tab, `overview` (the Overview is the bare slug), a view the tab doesn't take, a third segment. /companies/[slug]/value-chain
// is its own static route and wins over this one. Old ?tab=, ?basis=, ?window= and ?call= links are redirected in next.config.ts.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { companyMetadata, renderCompany } from "@/components/company/CompanyRoute";
import { pathParam } from "@/lib/equity/company";

type Props = PageProps<"/companies/[slug]/[...path]">;

// One sample for the build, the Overview's company (this page has no parent layout to take its slug from): no more build-time reads of production than the Overview already makes.
// Every other company, tab and view renders on its first request, then is cached.
export function generateStaticParams(): { slug: string; path: string[] }[] {
  return [{ slug: "adanient", path: ["valuation"] }];
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug, path } = await params;
  return pathParam(path) ? companyMetadata(slug) : { title: "Company · Finance Terminal" };
}

export default async function Page({ params }: Props) {
  const { slug, path } = await params;
  const target = pathParam(path);
  if (!target) notFound();
  return renderCompany(slug, target.tab, target.view);
}
