// /accuracy: "How often does this site hold something back, and do its checks work?" (#135; Plan 02 §4; Brief §8, §9).
// Counts by module for the last 30 days, 90 days and all time, from api.quality_summary (written by site.quality_summary),
// plus the passing evaluation runs from api.eval_summary. The period is in the URL (?period=), so it renders inside
// Suspense (the search params are only known on request), from cached reads (lib/queries/accuracy.ts).
import type { Metadata } from "next";
import { Suspense } from "react";
import { AccuracyView } from "@/components/accuracy/AccuracyView";
import { StateNote } from "@/components/data/StateNote";
import { parsePeriod } from "@/lib/accuracy/summary";
import { readEvalSummary, readQualitySummary } from "@/lib/queries/accuracy";
import { readFreshness } from "@/lib/queries/shell";

export const metadata: Metadata = {
  title: "Accuracy · Finance Terminal",
  description:
    "How often this site holds something back, and whether its checks work: AI outputs published or stopped, loads rolled back, freshness, drills and evaluation gates. Counts only.",
};

type Props = PageProps<"/accuracy">;

async function Accuracy({ searchParams }: { searchParams: Props["searchParams"] }) {
  const query = await searchParams;
  const [quality, evals, fresh] = await Promise.all([readQualitySummary(), readEvalSummary(), readFreshness()]);
  const now = fresh.ok ? fresh.readAt : new Date().toISOString();
  return (
    <AccuracyView
      data={{ period: parsePeriod(query.period), quality, evals, freshness: fresh.ok ? fresh.tasks : null, now }}
    />
  );
}

export default function Page({ searchParams }: Props) {
  return (
    <Suspense fallback={<StateNote kind="empty">Loading the counts…</StateNote>}>
      <Accuracy searchParams={searchParams} />
    </Suspense>
  );
}
