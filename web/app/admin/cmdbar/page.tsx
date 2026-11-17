// /admin/cmdbar (Spec 07 §4 Examples, §9 step 4; K-09 part 2, #588): the command bar's example answers, each marked checked once by
// the admin before the box shows it as a chip. Admin only; not indexed.
import type { Metadata } from "next";
import { CmdbarExamplesView } from "@/components/admin/CmdbarExamplesView";
import { PageHeader } from "@/components/data/PageHeader";

export const metadata: Metadata = { title: "Command bar examples · Finance Terminal", robots: { index: false, follow: false } };

export default function CmdbarExamplesPage() {
  return (
    <>
      <PageHeader
        crumbs={[{ label: "Admin", href: "/admin" }, "Command bar examples"]}
        title="Command bar examples"
        description="The example questions the command bar offers, each with its stored answer. Check each answer once: only checked answers appear as chips."
      />
      <div className="mt-5">
        <CmdbarExamplesView />
      </div>
    </>
  );
}
