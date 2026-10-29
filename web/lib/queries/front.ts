// web/lib/queries/front.ts: what the front page (/, #152) reads, built from the reads that already exist: the policy
// overview (each bank's latest decision with its votes and decision document), the freshness and next-meeting reads
// of the shell, and, for each bank, the stored diff of its latest decision document with the numbers that changed.
// Each bank's part fails on its own: one bank's failed read leaves the other's panel whole. Reads go through the
// browser key only (lib/queries/overview.ts, meeting.ts), are cached for minutes and log reasons to the server log,
// never to the visitor. Nothing here computes a figure; lib/policy/changes.ts assembles the rows.
import { fieldPolicy } from "@/components/data/Gated";
import { changesModel, numbersModel, type ChangesModel, type NumbersModel } from "@/lib/policy/changes";
import { readMeetingDiff, readMeetingPage, readNumbersBase } from "@/lib/queries/meeting";
import { readPolicyOverview, type LatestDecision, type PolicyOverview } from "@/lib/queries/overview";
import type { Bank } from "@/lib/queries/policy";
import { readFreshness, readNextMeetings, type Read } from "@/lib/queries/shell";
import type { FieldPolicyRow } from "@/lib/queries/api";

/** One bank's What changed at its latest decision, or why there is none to show. */
export type BankChanges =
  | { kind: "error" }
  | { kind: "no-decision" }
  | { kind: "no-document" }
  | { kind: "no-earlier" }
  | { kind: "ok"; model: ChangesModel; numbers: NumbersModel };

export async function bankChanges(bank: Bank, latest: Read<LatestDecision>, policies: FieldPolicyRow[] | null): Promise<BankChanges> {
  if (!latest.ok) return { kind: "error" };
  const m = latest.meeting;
  if (!m) return { kind: "no-decision" };
  const doc = latest.document;
  if (!doc) return { kind: "no-document" };
  try {
    const read = await readMeetingDiff(doc.id);
    if (!read.ok) return { kind: "error" };
    if (!read.diff) return { kind: "no-earlier" };
    const model = changesModel({ bank, m, decisionDoc: doc, diff: read.diff, base: read.base, summary: read.summary });
    // Numbers that changed: the RBI's only (its resolution carries the rates and projections; the Fed's table is the
    // meeting page's). A failed read leaves the section in its error state, not the bank's panel.
    let numbers: NumbersModel = { kind: "none" };
    if (bank === "RBI" && read.base?.event_slug) {
      const [page, earlier] = await Promise.all([readMeetingPage(bank, m.slug, m.decision_date), readNumbersBase(bank, read.base.event_slug)]);
      const stancePolicy = policies ? fieldPolicy(policies, "ai.stance_extraction") : null;
      numbers = numbersModel({
        m,
        base: read.base,
        current: page.settings.ok && page.votes.ok && page.projections.ok ? { settings: page.settings.rows, votes: page.votes.rows, projections: page.projections.rows } : null,
        earlier: earlier.ok ? earlier : null,
        stance: { show: stancePolicy !== null && stancePolicy.status !== "hidden", label: stancePolicy?.status === "labelled" ? stancePolicy.label : null },
      });
    }
    return { kind: "ok", model, numbers };
  } catch (error) {
    console.error(`[front] ${bank} changes failed: ${error instanceof Error ? error.message : String(error)}`);
    return { kind: "error" };
  }
}

export type Front = {
  overview: PolicyOverview;
  freshness: Awaited<ReturnType<typeof readFreshness>>;
  next: Awaited<ReturnType<typeof readNextMeetings>>;
  fed: BankChanges;
  rbi: BankChanges;
};

export async function readFront(): Promise<Front> {
  const [overview, freshness, next] = await Promise.all([readPolicyOverview(), readFreshness(), readNextMeetings()]);
  const policies = overview.fieldPolicy.ok ? overview.fieldPolicy.rows : null;
  const [fed, rbi] = await Promise.all([bankChanges("Fed", overview.fed, policies), bankChanges("RBI", overview.rbi, policies)]);
  return { overview, freshness, next, fed, rbi };
}
