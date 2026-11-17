// The command bar's example answers for the admin (Spec 07 §4 Examples, §9 step 4; K-09 part 2, #588): every chip with its stored
// answer, checked or not, read through `GET /api/admin/cmdbar/examples`, and the mark that lets a checked one reach the box
// (`POST /api/admin/cmdbar/examples/{id}/check`). The answers are the `cmdbar.examples` task's; nothing here asks a model.
import { adminFetch, type AdminDeps } from "@/lib/admin/api";
import { parseAnswer, type Answer } from "@/lib/cmdbar";

export type AdminExample = {
  id: number;
  question: string;
  module: string | null;
  displayOrder: number | null;
  /** The stored answer as the box would show it; null while the task hasn't stored one. */
  answer: Answer | null;
  /** True only for an answer (state `answered`): anything else can't be marked checked. */
  answerable: boolean;
  verified: boolean;
  /** When the task last wrote the answer (the time the admin's mark is tied to). */
  updatedAt: string | null;
};

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** The route's list, each row checked; null when the body isn't the route's shape. */
export function parseAdminExamples(body: unknown): AdminExample[] | null {
  if (!isObj(body) || !Array.isArray(body.examples)) return null;
  const out: AdminExample[] = [];
  for (const r of body.examples) {
    if (!isObj(r) || typeof r.id !== "number" || !Number.isInteger(r.id) || typeof r.question !== "string") continue;
    const stored = isObj(r.answer) ? r.answer : null;
    out.push({
      id: r.id,
      question: r.question,
      module: typeof r.module === "string" ? r.module : null,
      displayOrder: typeof r.display_order === "number" && Number.isInteger(r.display_order) ? r.display_order : null,
      answer: stored ? parseAnswer(stored) : null,
      answerable: stored !== null && stored.state === "answered",
      verified: r.verified === true,
      updatedAt: typeof r.updated_at === "string" ? r.updated_at : null,
    });
  }
  return out;
}

export type ReadResult = { examples: AdminExample[] | null; status: number };

export async function readAdminExamples(deps?: AdminDeps): Promise<ReadResult> {
  const r = await adminFetch<unknown>("/api/admin/cmdbar/examples", {}, deps);
  const examples = r.ok ? parseAdminExamples(r.body) : null;
  return { examples, status: examples ? r.status : r.ok ? 502 : r.status };
}

/** What a mark came to: done, the answer was rewritten since the page read it, it isn't an answer, or a failure (with status). */
export type CheckOutcome = "done" | "changed" | "not_answered" | "failed";

export async function markExample(example: AdminExample, verified: boolean, deps?: AdminDeps): Promise<{ outcome: CheckOutcome; status: number }> {
  const r = await adminFetch<unknown>(
    `/api/admin/cmdbar/examples/${example.id}/check`,
    { method: "POST", json: verified ? { verified, updated_at: example.updatedAt } : { verified } },
    deps,
  );
  if (r.ok) return { outcome: "done", status: r.status };
  const state = r.status === 409 && isObj(r.body) ? r.body.state : null;
  if (state === "changed" || state === "not_answered") return { outcome: state, status: r.status };
  return { outcome: "failed", status: r.status };
}
