// The body shared by the company page's routes (/companies/[slug] and /companies/[slug]/[...path]; R05-9b, 12 Nov 2026). The
// tab and its view come from the route's params only, never the query string, so the route is built ahead and refreshed in the
// background like Policy Day; every read here is a cached one. An unknown slug answers 404 from here, before anything renders
// (a response's status is fixed once it streams); a failed existence read is the error state, never a 404.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CompanyError, CompanyView } from "@/components/company/CompanyView";
import type { CompanyViewSelection, TabId } from "@/lib/equity/company";
import { readCompanyExists, readCompanyMarket, readCompanyName, readCompanyPage } from "@/lib/queries/company";
import { readFreshness } from "@/lib/queries/shell";

export async function renderCompany(slug: string, tab: TabId, view: CompanyViewSelection) {
  const exists = await readCompanyExists(slug);
  if (exists === false) notFound();
  if (exists === null) return <CompanyError />;
  const [data, fresh] = await Promise.all([readCompanyPage(slug, tab, view.call), readFreshness()]);
  if (!data.ok) return <CompanyError />;
  return <CompanyView data={data} basis={view.basis} window={view.window} freshness={fresh.ok ? fresh.tasks : null} />;
}

// The tab's title names the company (12 Nov 2026): every company page used to be "Company · Finance Terminal", so two
// open tabs, a bookmark or a shared link couldn't be told apart. An unknown slug or a failed read keeps the plain title.
// A US company's description names the Dow 30 and its sources (US-06, #405); India's is unchanged.
export async function companyMetadata(slug: string): Promise<Metadata> {
  const [name, market] = await Promise.all([readCompanyName(slug), readCompanyMarket(slug)]);
  return {
    title: `${name ?? "Company"} · Finance Terminal`,
    description:
      market === "US"
        ? "A Dow 30 company's price, key figures, peers, SEC filings and valuation, in dollars. Informational only, not investment advice."
        : "A Nifty 50 company's price, key figures, peers, filed financials and corporate actions. Informational only, not investment advice.",
  };
}
