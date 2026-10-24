// /admin/labelling (Prompt 01 E1; Architecture 03 §1.9): Anand labels Pass 2's evaluation sets, blind, one item at a
// time. Admin only; no model output on it. Not indexed.
import type { Metadata } from "next";
import { LabellingView } from "@/components/admin/LabellingView";
import { PageHeader } from "@/components/data/PageHeader";

export const metadata: Metadata = { title: "Labelling · Finance Terminal", robots: { index: false, follow: false } };

export default function LabellingPage() {
  return (
    <>
      <PageHeader
        crumbs={[{ label: "Admin", href: "/admin" }, "Labelling"]}
        title="Labelling"
        description="The evaluation sets for Pass 2's scores: a label and a one-line reason for each item, without seeing any model output."
      />
      <div className="mt-5">
        <LabellingView />
      </div>
    </>
  );
}
