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

  it("offers no Show all button when every meeting already fits", () => {
    expect(html).not.toContain("Show all");
    expect(html).not.toContain("aria-controls=");
  });
});

describe("a long history (#575)", () => {
  // 15 held decisions, newest first from Oct 2026 back one month at a time; each with its six stored votes.
  const dates = Array.from({ length: 15 }, (_, i) => {
    const d = new Date(Date.UTC(2026, 9 - i, 7));
    return d.toISOString().slice(0, 10);
  });
  const meetings = dates.map((d) => m(`mpc-${d}`, d, 0));
  const all = dates.flatMap((d) => votes.map((v) => ({ ...v, meeting_slug: `mpc-${d}`, is_dissent: false })));
  const long = renderToStaticMarkup(<VotesOverTimePanel data={votesOverTime("RBI", meetings, all)} footer={null} />);

  it("draws the latest 12 strips, newest first, and holds the 3 oldest back", () => {
    expect(long.match(/<li /g)).toHaveLength(12);
    expect(long).toContain(`/policy/rbi/${dates[0]}`);
    expect(long).toContain(`/policy/rbi/${dates[11]}`);
    expect(long).not.toContain(`/policy/rbi/${dates[12]}`);
    expect(long).not.toContain(`/policy/rbi/${dates[14]}`);
  });

  it("offers every meeting behind one button that names the count and the region it opens", () => {
    expect(long).toContain(">Show all 15 meetings<");
    expect(long).toContain('aria-expanded="false"');
    expect(long).toContain('aria-controls="votes-over-time-rbi-strips"');
    expect(long).toContain('id="votes-over-time-rbi-strips"');
    expect(long).toContain('aria-label="RBI votes, the latest 12 of 15 meetings, newest first"');
  });

  it("keeps every member and every meeting in the select and the table", () => {
    for (const n of six) expect(long).toContain(`>${n}</option>`);
    expect(votesOverTime("RBI", meetings, all).meetings).toHaveLength(15); // the grid's columns, drawn on "View as table"
  });
});

describe("/policy's gate for the section", () => {
  // The page is an async server component with live reads, so this reads its source, as freshness.test.ts does.
  const src = readFileSync(join(__dirname, "../../app/policy/page.tsx"), "utf8");
  it("renders the section only behind votesOverTimeShown, under the menu's section id", () => {
    expect(src).toMatch(/votesOverTimeShown\(policies\) && \(/);
    expect(src.indexOf("votesOverTime(bank")).toBeGreaterThan(src.indexOf("votesOverTimeShown(policies)"));
    // In the menu since it was flipped on production (13 Nov) and Anand asked for its link (15 Nov 2026, 17:18 IST, #575).
    expect(src).toContain("<Section id={POLICY_SECTIONS.votesOverTime.id} title={POLICY_SECTIONS.votesOverTime.title}>");
    expect(src.indexOf("POLICY_SECTIONS.votesOverTime.id")).toBeGreaterThan(src.indexOf("votesOverTimeShown(policies) && ("));
  });
});
