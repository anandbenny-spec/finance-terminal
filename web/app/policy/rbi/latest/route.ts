// /policy/rbi/latest: a 307 to the newest held MPC meeting, keeping a valid ?tab= (#147; Design 01 §5.6). The menu's
// "MPC: what changed" is /policy/rbi/latest?tab=changes. No page: a static segment wins over [date].
import type { NextRequest } from "next/server";
import { latestRedirect } from "@/lib/queries/latest";

export async function GET(request: NextRequest) {
  return latestRedirect("RBI", request);
}
