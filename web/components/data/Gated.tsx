// Gated (Design 01 §1.9, §4.3): renders a gated field as api.field_policy says: as is (`shown`), with its qualifier
// tag (`labelled`: "AI-extracted", "monthly", …) or not at all (`hidden`, and when no row exists: fail closed, as
// the database does). `heldBackNote` is the one honest line for a held-back feature worth naming.
import type { ReactNode } from "react";
import { Qualifier } from "@/components/data/Qualifier";
import { StateNote } from "@/components/data/StateNote";
import type { FieldPolicyRow } from "@/lib/queries/api";

export type FieldPolicies = readonly FieldPolicyRow[];

/** The policy for a field; null (hidden) when it has no row. */
export function fieldPolicy(policies: FieldPolicies, field: string): FieldPolicyRow | null {
  return policies.find((p) => p.field_key === field) ?? null;
}

export function Gated({
  policies,
  field,
  heldBackNote,
  showLabel = true,
  children,
}: {
  policies: FieldPolicies;
  field: string;
  heldBackNote?: string;
  /** False where the child already carries the label itself (an AIBlock's header names its kind). */
  showLabel?: boolean;
  children: ReactNode;
}) {
  const policy = fieldPolicy(policies, field);
  if (!policy || policy.status === "hidden") {
    return heldBackNote ? <StateNote kind="held">{heldBackNote}</StateNote> : null;
  }
  return (
    <>
      {children}
      {showLabel && policy.status === "labelled" && policy.label && <Qualifier className="ml-1.5 align-middle">{policy.label}</Qualifier>}
    </>
  );
}
