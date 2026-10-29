// ChangeStrip as the server sends it (Pass 1b, #89): the panel names its question, each bank's row opens with the newest
// bar's reading and a link to its What changed tab, "View as table" is there, and an empty or one-sided strip says so
// instead of going blank. Test rows, not real statements.
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ChangeStrip } from "@/components/ai/ChangeStrip";
import { TooltipProvider } from "@/components/ui/tooltip";
import { buildChangeStrips, stripScale } from "@/lib/policy/changestrip";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => undefined }) }));

const html = (node: ReactNode) => renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();

const decisions = [
  { slug: "fomc-2026-01-28", bank: "Fed" as const, decision_date: "2026-01-28" },
  { slug: "fomc-2026-03-18", bank: "Fed" as const, decision_date: "2026-03-18" },
  { slug: "mpc-2026-02-06", bank: "RBI" as const, decision_date: "2026-02-06" },
];
const documents = [
  { id: 1, event_slug: "fomc-2026-01-28", version_no: 1 },
  { id: 2, event_slug: "fomc-2026-03-18", version_no: 1 },
  { id: 3, event_slug: "mpc-2026-02-06", version_no: 1 },
];
const diff = (document_id: number, words_added: number, words_removed: number, old: number, nw: number) => ({ document_id, words_added, words_removed, words: { old, new: nw } });

describe("ChangeStrip", () => {
  const strips = buildChangeStrips(decisions, documents, [diff(1, 40, 20, 400, 400), diff(2, 100, 50, 400, 500), diff(3, 30, 60, 600, 600)]);
  const markup = html(<ChangeStrip strips={strips} scale={stripScale(strips)} footer={<span>Source line</span>} />);
  const t = text(markup);

  it("names the question it answers, and keeps its footer and the table switch", () => {
    expect(t).toContain("How much each statement changed");
    expect(t).toContain("Which statements were rewritten the most?");
    expect(t).toContain("Source line");
    expect(markup).toContain('aria-label="View as table"');
  });

  it("reads each bank's newest bar first, as shares with a true minus and the stored word counts", () => {
    expect(t).toContain("Fed (FOMC statements)");
    expect(t).toContain("RBI (MPC resolutions)");
    expect(t).toContain("+20.0% added (100 of 500 words)");
    expect(t).toContain("−12.5% removed (50 of 400 words)");
    expect(t).toContain("+5.0% added (30 of 600 words)");
    expect(t).toContain("−10.0% removed (60 of 600 words)");
  });

  it("links each row to its newest meeting's What changed tab", () => {
    expect(markup).toContain('href="/policy/fed/2026-03-18?tab=changes"');
    expect(markup).toContain('href="/policy/rbi/2026-02-06?tab=changes"');
  });

  it("labels the shares as derived and gives each row a keyboard way in", () => {
    expect(t).toContain("share of words, derived");
    expect(markup.match(/role="group"[^>]*tabindex="0"|tabindex="0"[^>]*role="group"/g)).toHaveLength(2);
  });

  it("counts meetings without a bar instead of drawing a zero", () => {
    const some = buildChangeStrips(decisions, documents, [diff(1, 40, 20, 400, 400), diff(3, 30, 60, 600, 600)]);
    const out = text(html(<ChangeStrip strips={some} scale={60} />));
    expect(out).toContain("1 of 2 Fed meetings has no bar");
  });

  it("says so when one bank has no stored comparison, and when neither does", () => {
    const oneSided = buildChangeStrips(decisions, documents, [diff(3, 30, 60, 600, 600)]);
    expect(text(html(<ChangeStrip strips={oneSided} scale={60} />))).toContain("No FOMC statement has a stored comparison");
    const none = buildChangeStrips(decisions, documents, []);
    const out = text(html(<ChangeStrip strips={none} scale={60} />));
    expect(out).toContain("No statement has a stored comparison with the one before it yet.");
    expect(out).not.toContain("View as table");
  });
});
