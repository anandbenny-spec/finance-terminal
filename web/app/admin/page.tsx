// /admin (C6; Spec 01 §4.4): the one admin's login and links. Not indexed; nothing here is visitor content.
import type { Metadata } from "next";
import { AdminHome } from "@/components/admin/AdminHome";
import { PageHeader } from "@/components/data/PageHeader";

export const metadata: Metadata = { title: "Admin · Finance Terminal", robots: { index: false, follow: false } };

export default function AdminPage() {
  return (
    <>
      <PageHeader crumbs={["Admin"]} title="Admin" description="Log in to see the tasks' health and refresh the data." />
      <div className="mt-5">
        <AdminHome />
      </div>
    </>
  );
}
