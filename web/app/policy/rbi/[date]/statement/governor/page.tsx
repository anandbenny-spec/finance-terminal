// /policy/rbi/[date]/statement/governor: the Governor's statement beside the MPC resolution (R05-9a, 12 Nov 2026). A static
// segment, so it wins over /policy/rbi/[date]/[tab]; the Fed has no such page (/policy/fed/…/statement/governor answers 404).
import type { Metadata } from "next";
import { renderMeeting } from "@/components/policy/MeetingRoute";
import { formatDate } from "@/lib/format";
import { slugFor } from "@/lib/policy/meeting";

type Props = PageProps<"/policy/rbi/[date]/statement/governor">;

export function generateStaticParams(): { date: string }[] {
  return [{ date: "2018-12-05" }];
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { date } = await params;
  if (!slugFor("RBI", date)) return { title: "Not found · Finance Terminal" };
  return { title: `Governor's statement: MPC meeting, ${formatDate(date)} · Finance Terminal` };
}

export default async function Page({ params }: Props) {
  const { date } = await params;
  return renderMeeting("RBI", date, "statement", "governor");
}
