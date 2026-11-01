// The RSS feed (#252): rows in, an RSS 2.0 document out. Test rows shaped like the views; names and figures are test
// data, not a record of real meetings.
import { describe, expect, it } from "vitest";
import type { PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import { FEED_ITEMS, buildRss, escapeXml, feedItems, guidOf, pubDate } from "./rss";

function meeting(over: Partial<PolicyMeetingRow>): PolicyMeetingRow {
  return {
    slug: "fomc-2026-09-17", bank: "Fed", status: "held", decision_date: "2026-09-17", decision_at: "2026-09-17T18:00:00+00:00",
    kind: "scheduled", is_rate_decision: true, meeting_no: null, minutes_due_on: "2026-10-08", policy_rate_pct: 3.5,
    policy_rate_change_bps: -25, stance: null, stance_display_label: null, dissent_count: 0,
    attribution: "Source: Federal Reserve Board", fed_target_lower_pct: 3.25, fed_target_upper_pct: 3.5, ...over,
  };
}
const rbi = (over: Partial<PolicyMeetingRow> = {}) =>
  meeting({ slug: "mpc-2026-10-07", bank: "RBI", decision_date: "2026-10-07", decision_at: "2026-10-07T04:30:00+00:00", meeting_no: 60, minutes_due_on: "2026-10-21", policy_rate_pct: 5.25, policy_rate_change_bps: 0, fed_target_lower_pct: null, fed_target_upper_pct: null, attribution: "Source: Reserve Bank of India", ...over });

function vote(slug: string, name: string, bps: number | null, over: Partial<PolicyVoteRow> = {}): PolicyVoteRow {
  return { meeting_slug: slug, member_slug: name.toLowerCase(), member_name: name, voted_change_bps: bps, stance_vote: null, is_dissent: false, dissent_text: null, ...over };
}

const ORIGIN = new URL("https://finance-terminal.example");
const BUILT = new Date("2026-11-02T10:00:00Z");

describe("feedItems", () => {
  it("lists both banks, newest first", () => {
    const items = feedItems([meeting({}), rbi(), meeting({ slug: "fomc-2026-07-29", decision_date: "2026-07-29" })], [], ORIGIN);
    expect(items.map((i) => i.title)).toEqual(["RBI: Held at 5.25%", "Fed: Cut 25 bps to 3.25–3.50%", "Fed: Cut 25 bps to 3.25–3.50%"]);
    expect(items.map((i) => i.guid)).toEqual(["finance-terminal:policy:mpc-2026-10-07", "finance-terminal:policy:fomc-2026-09-17", "finance-terminal:policy:fomc-2026-07-29"]);
  });

  it("carries at most 20 items", () => {
    const many = Array.from({ length: 30 }, (_, i) => meeting({ slug: `fomc-${i}`, decision_date: `2026-01-${String(i + 1).padStart(2, "0")}` }));
    const items = feedItems(many, [], ORIGIN);
    expect(items).toHaveLength(FEED_ITEMS);
    expect(items[0].guid).toBe("finance-terminal:policy:fomc-29");
  });

  it("leaves out a meeting with no decision line, and still fills 20 from the rest", () => {
    const rows = [meeting({ slug: "no-change", decision_date: "2026-12-01", policy_rate_change_bps: null }), ...Array.from({ length: 20 }, (_, i) => meeting({ slug: `m${i}`, decision_date: `2026-02-${String(i + 1).padStart(2, "0")}` }))];
    const items = feedItems(rows, [], ORIGIN);
    expect(items).toHaveLength(20);
    expect(items.some((i) => i.guid.endsWith("no-change"))).toBe(false);
  });

  it("links to the meeting's What changed tab, absolute", () => {
    const [item] = feedItems([meeting({})], [], ORIGIN);
    expect(item.link).toBe("https://finance-terminal.example/policy/fed/2026-09-17?tab=changes");
  });

  it("describes the Fed vote with its dissenters, and no more", () => {
    const votes = [vote("fomc-2026-09-17", "A", -25), vote("fomc-2026-09-17", "B", -25), vote("fomc-2026-09-17", "C", -50, { is_dissent: true })];
    const [item] = feedItems([meeting({})], votes, ORIGIN);
    expect(item.description).toBe("Decided 17 Sep 2026. Vote 2–1 · Dissent: C (preferred −50 bps)");
  });

  it("gives an RBI meeting before its minutes the pending note", () => {
    const [item] = feedItems([rbi()], [], ORIGIN);
    expect(item.description).toBe("Decided 7 Oct 2026. Each member's vote is published in the minutes, due 21 Oct 2026.");
  });

  it("writes the RBI rate vote only: no stance count, no stance dissent, no stance label", () => {
    const names = ["A", "B", "C", "D", "E", "F"];
    const votes = names.map((n, i) => vote("mpc-2026-10-07", n, 0, { stance_vote: i < 5 ? "neutral" : "accommodative", is_dissent: i === 5 }));
    const m = rbi({ stance: "neutral", stance_display_label: "Neutral" });
    const xml = buildRss([m], votes, ORIGIN, BUILT);
    expect(xml).toContain("Rate 6–0");
    expect(xml).not.toMatch(/stance|Stance|neutral|Neutral|accommodative|Dissent/);
  });

  it("states no stance and no AI text from a meeting whose stance is set", () => {
    const xml = buildRss([meeting({ stance: "hawkish", stance_display_label: "Hawkish" })], [], ORIGIN, BUILT);
    expect(xml).not.toMatch(/hawkish|Hawkish|stance/i);
  });

  it("keeps the guid when the origin changes", () => {
    const a = feedItems([meeting({})], [], ORIGIN)[0];
    const b = feedItems([meeting({})], [], new URL("https://other.example"))[0];
    expect(a.guid).toBe(b.guid);
    expect(a.guid).toBe(guidOf("fomc-2026-09-17"));
    expect(a.link).not.toBe(b.link);
  });
});

describe("pubDate (RFC 822)", () => {
  it("uses the stored decision instant", () => {
    expect(pubDate(meeting({}))).toBe("Thu, 17 Sep 2026 18:00:00 GMT");
  });
  it("uses the decision date at 00:00 in the bank's zone when the instant is null", () => {
    expect(pubDate(meeting({ decision_at: null }))).toBe("Thu, 17 Sep 2026 04:00:00 GMT"); // EDT, UTC−4
    expect(pubDate(meeting({ decision_at: null, decision_date: "2026-12-09" }))).toBe("Wed, 09 Dec 2026 05:00:00 GMT"); // EST, UTC−5
    expect(pubDate(rbi({ decision_at: null }))).toBe("Tue, 06 Oct 2026 18:30:00 GMT"); // IST, UTC+5:30
  });
});

describe("buildRss", () => {
  it("writes the channel", () => {
    const xml = buildRss([meeting({})], [], ORIGIN, BUILT);
    expect(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"')).toBe(true);
    expect(xml).toContain("<title>Finance Terminal</title>");
    expect(xml).toContain("<link>https://finance-terminal.example/</link>");
    expect(xml).toContain("<language>en</language>");
    expect(xml).toContain("<lastBuildDate>Mon, 02 Nov 2026 10:00:00 GMT</lastBuildDate>");
    expect(xml).toContain('<atom:link href="https://finance-terminal.example/feed.xml" rel="self" type="application/rss+xml" />');
    expect(xml).toContain('<guid isPermaLink="false">finance-terminal:policy:fomc-2026-09-17</guid>');
    expect(xml).toContain("<pubDate>Thu, 17 Sep 2026 18:00:00 GMT</pubDate>");
  });

  it("escapes every text value", () => {
    expect(escapeXml(`a & b < c > "d" 'e'`)).toBe("a &amp; b &lt; c &gt; &quot;d&quot; &apos;e&apos;");
    const xml = buildRss([meeting({ slug: "x&y<z" })], [], ORIGIN, BUILT);
    expect(xml).toContain("finance-terminal:policy:x&amp;y&lt;z");
    expect(xml).not.toContain("x&y<z");
  });
});
