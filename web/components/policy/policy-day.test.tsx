// Policy Day's body (#90): the Fed / RBI switch, the decision card with the bank's own latest decision, the countdown
// panel and the meeting panels for that decision. The reads and the meeting panels are mocked: they have their own tests.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { PolicyOverview } from "@/lib/queries/overview";

vi.mock("@/lib/queries/shell", () => ({
  readNextMeetings: async () => ({ ok: true, meetings: [] }),
  readFreshness: async () => ({ ok: true, tasks: [], readAt: "2026-11-07T12:00:00Z" }),
}));
vi.mock("@/components/policy/MeetingView", () => ({
  PolicyDayMeeting: ({ bank, slug, date }: { bank: string; slug: string; date: string }) => <p>meeting panels {bank} {slug} {date}</p>,
}));

const { PolicyDay } = await import("@/components/policy/PolicyDay");

const meeting = (bank: "Fed" | "RBI", date: string) =>
  ({ bank, slug: `${bank === "Fed" ? "fomc" : "mpc"}-${date}`, decision_date: date, decision_at: null, status: "held", kind: "scheduled", is_rate_decision: true, attribution: null }) as never;
const latest = (bank: "Fed" | "RBI", date: string) => ({ ok: true as const, meeting: meeting(bank, date), votes: [], document: null, summary: null, stance: null });
const overview = {
  readAt: "2026-11-07T12:00:00Z",
  fieldPolicy: { ok: true, rows: [] },
  fed: latest("Fed", "2026-10-28"),
  rbi: latest("RBI", "2026-10-07"),
} as unknown as PolicyOverview;

describe("PolicyDay", () => {
  it("shows the Fed's latest decision and meeting panels, with the switch marking the Fed", async () => {
    const html = renderToStaticMarkup(await PolicyDay({ bank: "Fed", overview }));
    expect(html).toContain("Policy Day");
    expect(html).toContain("Latest Fed decision");
    expect(html).toContain("meeting panels Fed fomc-2026-10-28 2026-10-28");
    expect(html).toContain('aria-current="page"');
    expect(html).toContain('href="/workspace/policy-day/rbi"');
    expect(html).not.toContain("?bank=");
    expect(html).toContain("Next FOMC meeting");
  });

  it("gives the card 8 of 12 columns and lets the countdown keep its own height (12 Nov 2026)", async () => {
    const html = renderToStaticMarkup(await PolicyDay({ bank: "Fed", overview }));
    const section = (title: string) => html.slice(html.lastIndexOf("<section", html.indexOf(title)), html.indexOf(title));
    expect(section("Latest Fed decision")).toContain("xl:col-span-8");
    const countdown = section("Next FOMC meeting");
    expect(countdown).toContain("xl:col-span-4");
    expect(countdown).toContain("self-start");
  });

  it("shows the RBI's when asked, never the Fed's", async () => {
    const html = renderToStaticMarkup(await PolicyDay({ bank: "RBI", overview }));
    expect(html).toContain("Latest RBI decision");
    expect(html).toContain("meeting panels RBI mpc-2026-10-07 2026-10-07");
    expect(html).not.toContain("fomc-2026-10-28");
  });

  it("keeps a panel (no blank page) when the decision can't be read", async () => {
    const html = renderToStaticMarkup(await PolicyDay({ bank: "Fed", overview: { ...overview, fed: { ok: false } } as unknown as PolicyOverview }));
    expect(html).toContain("Couldn&#x27;t load this panel");
    expect(html).not.toContain("meeting panels");
  });
});
