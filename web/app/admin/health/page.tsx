// /admin/health (C6; Spec 01 §4.4; Design 01 §4.7): the tasks' health, open alerts and recent runs, read through
// /api/admin/health with the admin's token (ops is invisible to the browser key). Not indexed.
import type { Metadata } from "next";
import { HealthView } from "@/components/admin/HealthView";
import { PageHeader } from "@/components/data/PageHeader";

export const metadata: Metadata = { title: "Health · Finance Terminal", robots: { index: false, follow: false } };

export default function HealthPage() {
  return (
    <>
      <PageHeader
        crumbs={[{ label: "Admin", href: "/admin" }, "Health"]}
        title="Health"
        description="Every task's last success and status, the open alerts (oldest first) and the recent runs."
      />
      <div className="mt-5">
        <HealthView />
      </div>
    </>
  );
}
