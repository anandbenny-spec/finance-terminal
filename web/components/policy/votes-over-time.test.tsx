// VotesOverTimePanel (#508): the strips, the Member select, the pending row, "View as table", and the page's gate.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { VotesOverTimePanel } from "@/components/policy/VotesOverTime";
import { votesOverTime } from "@/lib/policy/votes-over-time";
import type { PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";

const m = (slug: string, date: string, bps: number): PolicyMeetingRow => ({
  slug, bank: "RBI", status: "held", decision_date: date, decision_at: null, kind: "scheduled", is_rate_decision: true, meeting_no: null,
  minutes_due_on: "2026-10-21", policy_rate_pct: null, policy_rate_change_bps: bps, stance: null, stance_display_label: null,
  dissent_count: 0, attribution: null, fed_target_lower_pct: null, fed_target_upper_pct: null,
});
const six = ["Das", "Varma", "Patra", "Goyal", "Bhide", "Kumar"];
const votes: PolicyVoteRow[] = six.map((n, i) => ({
  meeting_slug: "mpc-2026-08-06", member_slug: n.toLowerCase(), member_name: n, voted_change_bps: 0, stance_vote: "neutral",
  is_dissent: i === 0, dissent_text: null, document_id: 3, char_start: 1, char_end: 4,
}));
const data = votesOverTime("RBI", [m("mpc-2026-10-07", "2026-10-07", 0), m("mpc-2026-08-06", "2026-08-06", 0)], votes);
const html = renderToStaticMarkup(<VotesOverTimePanel data={data} footer={<span>foot</span>} />);

describe("VotesOverTimePanel", () => {
  it("draws one strip per meeting, newest first, each linking to its meeting page", () => {
    expect(html).toContain("RBI: how members voted over time");
    expect(html.indexOf("/policy/rbi/2026-10-07")).toBeLessThan(html.indexOf("/policy/rbi/2026-08-06"));
    expect(html.match(/<li /g)).toHaveLength(2);
    expect(html).toContain("bg-series-2"); // the bank's own colour, edge included
    expect(html).toContain("hollow dot: dissent not on the rate"); // Das's stance-only dissent
    expect(html).toContain("border border-series-2 bg-panel");
  });

  it("shows the pending meeting as words, with no dots in its strip", () => {
    const first = html.slice(html.indexOf("<li "), html.indexOf("</li>"));
    expect(first).toContain("minutes");
    expect(first).not.toContain("<button");
  });

  it("offers a Member select with every member, and View as table", () => {
    expect(html).toContain("All members");
    for (const n of six) expect(html).toContain(`>${n}</option>`);
    expect(html).toContain('aria-label="View as table"');
  });

  it("keeps the dots out of the tab order until a member is chosen, and uses no hue for a vote", () => {
    expect(html).toContain('tabindex="-1"');
    expect(html).not.toMatch(/text-(green|red)|bg-(green|red)|\b(up|down)\b-?color/);
  });

  it("holds long histories inside the panel: a named scroll region, no fixed widths", () => {
    expect(html).toContain('role="region"');
    expect(html).toContain("max-h-(--table-max-h)");
    expect(html).not.toMatch(/min-w-\[|w-\[\d+px\]/);
  });

  it("says so when a bank has no decisions", () => {
    const none = renderToStaticMarkup(<VotesOverTimePanel data={votesOverTime("Fed", [], [])} footer={null} />);
    expect(none).toContain("No decisions are on the site yet.");
  });
});

describe("/policy's gate for the section", () => {
  // The page is an async server component with live reads, so this reads its source, as freshness.test.ts does.
  const src = readFileSync(join(__dirname, "../../app/policy/page.tsx"), "utf8");
  it("renders the section only behind votesOverTimeShown, and adds no nav or section link", () => {
    expect(src).toMatch(/votesOverTimeShown\(policies\) && \(/);
    expect(src.indexOf("votesOverTime(bank")).toBeGreaterThan(src.indexOf("votesOverTimeShown(policies)"));
    const sections = readFileSync(join(__dirname, "../../lib/policy/sections.ts"), "utf8");
    expect(sections).not.toContain("votes-over-time");
  });
});
