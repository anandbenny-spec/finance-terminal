// /earnings: what is the latest earnings call of each Nifty 50 company? (2B-20, #183; Spec 02B §13.7). One table, a row per
// company from `api.earnings_latest` (lib/queries/calls.ts, read whole against its exact count), formatted by lib/format.ts.
// Informational only: no verdicts on management (Spec 02B §8's words never used). The calls tasks join the status bar's
// count in 2B-22 (lib/feeds.ts PREVIEW_FEEDS).
import type { Metadata } from "next";
import { EarningsList } from "@/components/company/calls/EarningsList";
import { readEarningsListPage } from "@/lib/queries/calls";
import { readFreshness } from "@/lib/queries/shell";

export const metadata: Metadata = {
  title: "Earnings calls · Finance Terminal",
  description: "The latest earnings call of each Nifty 50 company, with its AI summary and the figures management gave. Informational only, not investment advice.",
};

export default async function EarningsPage() {
  const [data, fresh] = await Promise.all([readEarningsListPage(), readFreshness()]);
  return <EarningsList data={data} freshness={fresh.ok ? fresh.tasks : null} />;
}
