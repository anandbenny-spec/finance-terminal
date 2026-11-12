// /companies/us-<ticker>/value-chain (US-06, #405; Spec 02A.1 §1, §7): the value chain is read from Indian filings (BRSR and
// related-party disclosures), so a US company has none. Its page isn't linked; a deep link shows the company's header and one
// StateNote with the reason, and reads none of the value chain's views.
import Link from "next/link";
import { CompanyHeader } from "@/components/company/CompanyHeader";
import { PageHeader } from "@/components/data/PageHeader";
import { Panel } from "@/components/data/Panel";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { SEC_EDGAR, VALUE_CHAIN_NOT_COVERED_US, YAHOO, dayChange } from "@/lib/equity/company";
import type { CompanyRow, CorporateActionRow } from "@/lib/queries/api";

export function ValueChainNotCovered({
  company,
  actions,
  indexNames,
}: {
  company: CompanyRow;
  actions: CorporateActionRow[];
  indexNames: Record<string, string>;
}) {
  const name = company.short_name ?? company.name;
  return (
    <>
      <PageHeader
        crumbs={["Company research", { label: "Screener", href: "/screener?market=us" }, { label: name, href: `/companies/${company.slug}` }, "Value chain"]}
        title={`${company.name}: value chain`}
        description="Where do the company's inputs come from, how does it reach its customers, and how much of that runs through its own group?"
        meta={<SourceLine lines={[SEC_EDGAR, YAHOO]} />}
      />
      <CompanyHeader company={company} day={dayChange(company, actions)} indexNames={indexNames} />
      <p className="mt-4 text-data">
        <Link href={`/companies/${company.slug}`} className="text-ink-1 underline hover:text-ink-2">
          ‹ Back to {name}
        </Link>
      </p>
      <div className="mt-4">
        <Panel title="Value chain" titleId="not-covered">
          <StateNote kind="unavailable">{VALUE_CHAIN_NOT_COVERED_US}</StateNote>
        </Panel>
      </div>
    </>
  );
}
