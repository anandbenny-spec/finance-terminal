// Module 1's ChangeStrip output, pinned (#394, 2B-19e): the markup the Fed and RBI panel renders is stored in
// __snapshots__ and must stay byte-for-byte the same when the row becomes a reusable part. Test rows, not real statements.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ChangeStrip } from "@/components/ai/ChangeStrip";
import { TooltipProvider } from "@/components/ui/tooltip";
import { buildChangeStrips, stripScale } from "@/lib/policy/changestrip";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => undefined }) }));

const html = (node: ReactNode) => renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);

const decisions = [
  { slug: "fomc-2026-01-28", bank: "Fed" as const, decision_date: "2026-01-28" },
  { slug: "fomc-2026-03-18", bank: "Fed" as const, decision_date: "2026-03-18" },
  { slug: "mpc-2026-02-06", bank: "RBI" as const, decision_date: "2026-02-06" },
  { slug: "mpc-2026-04-08", bank: "RBI" as const, decision_date: "2026-04-08" },
];
const documents = [
  { id: 1, event_slug: "fomc-2026-01-28", version_no: 1 },
  { id: 2, event_slug: "fomc-2026-03-18", version_no: 1 },
  { id: 3, event_slug: "mpc-2026-02-06", version_no: 1 },
  { id: 4, event_slug: "mpc-2026-04-08", version_no: 1 },
];
const diff = (document_id: number, words_added: number, words_removed: number, old: number, nw: number) => ({ document_id, words_added, words_removed, words: { old, new: nw } });

describe("ChangeStrip markup, pinned", () => {
  it("both banks, with a footer and a refresh list", () => {
    const strips = buildChangeStrips(decisions, documents, [diff(1, 40, 20, 400, 400), diff(2, 100, 50, 400, 500), diff(3, 30, 60, 600, 600), diff(4, 90, 10, 600, 700)]);
    expect(html(<ChangeStrip strips={strips} scale={stripScale(strips)} footer={<span>Source line</span>} />)).toMatchSnapshot();
  });
  it("one bank with bars and meetings without a bar, the other with no bar at all", () => {
    const strips = buildChangeStrips(decisions, documents, [diff(1, 40, 20, 400, 400)]);
    expect(html(<ChangeStrip strips={strips} scale={60} />)).toMatchSnapshot();
  });
  it("the all-empty panel", () => {
    const strips = buildChangeStrips(decisions, documents, []);
    expect(html(<ChangeStrip strips={strips} scale={60} />)).toMatchSnapshot();
  });
});
