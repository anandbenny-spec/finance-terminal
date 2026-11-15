// LanguageTimelinePanel (#509, marks reworked in #530): one table per bank, the phrase column sticky, a sentence per cell
// with its new words underlined, its removed words struck and its ¶ link, a gap that says why, and the page's gate.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LanguageTimelinePanel } from "@/components/policy/LanguageTimeline";
import type { BankTimeline } from "@/lib/policy/timeline";

const rbi: BankTimeline = {
  bank: "RBI",
  rows: [{ key: "stance", label: "Stance" }, { key: "inflation", label: "How inflation is described" }, { key: "decision", label: "Rate decision" }],
  latest: "2026-10-07",
  columns: [
    {
      slug: "mpc-2026-10-07", date: "2026-10-07", href: "/policy/rbi/2026-10-07/statement",
      cells: {
        stance: { kind: "sentence", how: "changed", para: 3, parts: [{ op: "eq", text: "The MPC also decided to change the stance to " }, { op: "ins", text: "calibrated tightening" }, { op: "eq", text: "." }] },
        inflation: { kind: "gap", reason: "several", matched: 2 },
        // Words were only removed since August: still "Changed", with the struck word shown.
        decision: { kind: "sentence", how: "changed", para: null, parts: [{ op: "eq", text: "The repo rate stays" }, { op: "del", text: " unchanged" }, { op: "eq", text: " at 5.5 per cent." }] },
      },
    },
    {
      slug: "mpc-2026-08-06", date: "2026-08-06", href: "/policy/rbi/2026-08-06/statement",
      cells: {
        stance: { kind: "sentence", how: "same", para: 4, parts: [{ op: "eq", text: "The MPC decided to remain neutral." }] },
        inflation: { kind: "sentence", how: "new", para: null, parts: [{ op: "ins", text: "8. CPI inflation increased to 4.8 per cent in August 2026." }] },
        decision: { kind: "sentence", how: "same", para: null, parts: [{ op: "eq", text: "The repo rate stays unchanged at 5.5 per cent." }] },
      },
    },
    {
      slug: "mpc-2026-06-05", date: "2026-06-05", href: "/policy/rbi/2026-06-05/statement",
      cells: {
        stance: { kind: "gap", reason: "no_diff", matched: 0 },
        inflation: { kind: "gap", reason: "none", matched: 0 },
        decision: { kind: "sentence", how: "same", para: null, parts: [{ op: "eq", text: "The repo rate stays unchanged at 5.5 per cent." }] },
      },
    },
  ],
};

const html = renderToStaticMarkup(<LanguageTimelinePanel data={rbi} footer={<span>foot</span>} />);

describe("LanguageTimelinePanel", () => {
  it("is the bank's panel with its market edge, its question and 'Since 2019'", () => {
    expect(html).toContain("RBI: how the wording moved");
    expect(html).toContain("border-t-series-2"); // India and the RBI: violet
    expect(html).toContain("Since 2019");
    expect(html).toContain("meeting by meeting");
    const fed = renderToStaticMarkup(<LanguageTimelinePanel data={{ ...rbi, bank: "Fed" }} footer={null} />);
    expect(fed).toContain("Fed: how the wording moved");
    expect(fed).toContain("border-t-series-1"); // the US and the Fed: cyan
  });

  it("has one column per meeting, newest first, after the phrase column, and one row per phrase", () => {
    const heads = [...html.matchAll(/<th[^>]*scope="col"[^>]*>(.*?)<\/th>/g)].map((m) => m[1]);
    expect(heads).toEqual(["Phrase", "07 Oct 2026", "06 Aug 2026", "05 Jun 2026"]);
    expect(html.match(/<tbody>[\s\S]*<\/tbody>/)![0].match(/<tr /g)).toHaveLength(3);
    expect(html).toContain(">Stance<");
    expect(html).toContain(">How inflation is described<");
    expect(html).toContain(">Rate decision<");
  });

  it("underlines the words new since the meeting before and strikes the words removed since then, and only those", () => {
    expect(html).toContain("<ins>calibrated tightening</ins>");
    expect(html).toContain("<span>The MPC also decided to change the stance to </span><ins>calibrated tightening</ins><span>.</span>");
    expect(html.match(/<ins>/g)).toHaveLength(2); // the stance's new words and the wholly new inflation sentence
    expect(html).toContain("<span>The repo rate stays</span><del> unchanged</del><span> at 5.5 per cent.</span>");
    expect(html.match(/<del>/g)).toHaveLength(1); // the one struck word; an unchanged sentence has neither mark
  });

  it("shows a changed sentence in ink-1 as 'Changed', an unchanged one faded as 'Unchanged', a new one as such", () => {
    const cell = (text: string) => html.slice(html.lastIndexOf('<div class="w-72', html.indexOf(text)), html.indexOf("</div>", html.indexOf(text)));
    expect(cell("calibrated tightening")).toMatch(/text-ink-1/);
    expect(cell("calibrated tightening")).toContain("Changed");
    expect(cell("The MPC decided to remain neutral.")).toMatch(/text-ink-3/);
    expect(cell("The MPC decided to remain neutral.")).toContain("Unchanged");
    expect(cell("8. CPI inflation")).toContain("New sentence");
    // Words only removed is a change, never "Unchanged".
    expect(cell("The repo rate stays")).toContain("Changed");
    expect(cell("The repo rate stays")).not.toContain("Unchanged");
    expect(cell("The repo rate stays")).toMatch(/text-ink-1/);
  });

  it("links ¶n to the paragraph in the meeting's own Statement tab, only where the paragraph is known", () => {
    expect(html).toContain('href="/policy/rbi/2026-10-07/statement#p-3"');
    expect(html).toContain('href="/policy/rbi/2026-08-06/statement#p-4"');
    expect(html).toContain("¶3");
    expect(html).not.toContain("#p-null");
    expect(html.match(/#p-/g)).toHaveLength(2);
  });

  it("says why a cell has no sentence", () => {
    expect(html).toContain("2 sentences match, so none is chosen.");
    expect(html).toContain("None of this meeting&#x27;s sentences matches.");
    expect(html).toContain("There is no earlier document to compare it with.");
    expect(html.match(/No single matching sentence/g)).toHaveLength(2);
  });

  it("never links out (RBI's pages least of all), and uses no hue to judge a wording", () => {
    expect(html).not.toMatch(/href="https?:/);
    expect(html).not.toMatch(/rbi\.org\.in/i);
    expect(html).not.toMatch(/text-(green|red)|bg-(green|red)|text-up|text-down/);
  });

  it("scrolls the meetings sideways inside its panel: a named region, the phrase column sticky, no fixed pixel widths", () => {
    expect(html).toContain('role="region"');
    expect(html).toContain("overflow-x-auto");
    expect(html).toContain("sticky left-0");
    expect(html).toContain("overflow-hidden"); // the panel clips what the table scrolls
    expect(html).toContain("align-top");
    expect(html).not.toMatch(/min-w-\[|w-\[\d+px\]/);
  });

  it("says so when a bank has no decisions", () => {
    const none = renderToStaticMarkup(<LanguageTimelinePanel data={{ ...rbi, columns: [], latest: null }} footer={null} />);
    expect(none).toContain("No decisions are on the site yet.");
    expect(none).not.toContain("<table");
  });
});

describe("/policy's gate for the section", () => {
  // The page is an async server component with live reads, so this reads its source, as the other gates' tests do.
  const src = readFileSync(join(__dirname, "../../app/policy/page.tsx"), "utf8");
  it("reads and renders the section only behind languageTimelineShown, and adds no nav or section link", () => {
    expect(src).toMatch(/const timeline = languageTimelineShown\(policies\) \? await readLanguageTimeline\(\) : null;/);
    expect(src).toMatch(/\{timeline && \(/);
    expect(src.match(/readLanguageTimeline\(\)/g)).toHaveLength(1);
    expect(src.indexOf("readLanguageTimeline()")).toBeGreaterThan(src.indexOf("languageTimelineShown(policies)"));
    expect(src.indexOf('id="language-timeline"')).toBeGreaterThan(src.indexOf('id="votes-over-time"'));
    expect(src.indexOf('id="language-timeline"')).toBeLessThan(src.indexOf("POLICY_SECTIONS.history"));
    const sections = readFileSync(join(__dirname, "../../lib/policy/sections.ts"), "utf8");
    expect(sections).not.toContain("language-timeline");
  });

  it("is read at most once a page, and is the only new read the page makes", () => {
    expect(src.match(/from "@\/lib\/queries\//g)).toHaveLength(4); // overview, policy (POLICY_START), shell and timeline
  });
});
