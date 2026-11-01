// /feed.xml (#252): a good read answers 200 with the RSS content type and an hour's edge cache; a failed read answers 503
// with no Cache-Control, never an empty feed. The route is the real module; only the cached read is a stand-in.
import { beforeEach, describe, expect, it, vi } from "vitest";

const read = vi.fn();
vi.mock("@/lib/queries/feed", () => ({ readFeedDecisions: () => read() }));

const { GET } = await import("./route");

const decision = {
  slug: "fomc-2026-09-17", bank: "Fed", status: "held", decision_date: "2026-09-17", decision_at: "2026-09-17T18:00:00+00:00",
  kind: "scheduled", is_rate_decision: true, meeting_no: null, minutes_due_on: "2026-10-08", policy_rate_pct: 3.5,
  policy_rate_change_bps: -25, stance: null, stance_display_label: null, dissent_count: 0, attribution: null,
  fed_target_lower_pct: 3.25, fed_target_upper_pct: 3.5,
};

describe("GET /feed.xml", () => {
  beforeEach(() => read.mockReset());

  it("answers 200 with the RSS type and the edge cache", async () => {
    read.mockResolvedValue({ ok: true, decisions: [decision], votes: [], readAt: "2026-11-02T10:00:00.000Z" });
    const res = await GET();
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/rss+xml; charset=utf-8");
    expect(res.headers.get("cache-control")).toBe("public, s-maxage=3600, stale-while-revalidate=86400");
    const body = await res.text();
    expect(body).toContain("<item>");
    expect(body).toContain("Fed: Cut 25 bps to 3.25–3.50%");
  });

  it("answers 503 with no Cache-Control when the read fails, and says nothing about why", async () => {
    read.mockResolvedValue({ ok: false });
    const res = await GET();
    expect(res.status).toBe(503);
    expect(res.headers.get("cache-control")).toBeNull();
    expect(await res.text()).toBe("The feed is unavailable right now.");
  });
});
