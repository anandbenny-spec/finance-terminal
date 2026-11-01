// web/lib/rss.ts: the site's RSS feed (#252; Anand, 1 Nov 2026): one item per held FOMC or MPC rate decision, built
// from rows the site already publishes. Pure: rows in, an RSS 2.0 document out; the reads live in lib/queries/feed.ts.
//
// - Automatic items only (no site announcements). A decision without a decision line (its rate or change isn't stored)
//   is left out: absent, not guessed (CLAUDE.md §1).
// - No AI text and no stance (a feed reader can't show citations; the site shows no AI line it can't cite), and no
//   document text or quotes: items carry the site's own facts and link to its own pages.
// - Titles and dates go through lib/policy/decisions.ts and lib/format.ts; only the machine dates (pubDate,
//   lastBuildDate) are written here, in RFC 822 as the format requires.
import { formatDate } from "@/lib/format";
import { decisionLine, meetingHref, rateVoteLine } from "@/lib/policy/decisions";
import type { PolicyMeetingRow, PolicyVoteRow } from "@/lib/queries/api";
import type { Bank } from "@/lib/queries/policy";
import { SITE_NAME, SITE_TAGLINE } from "@/lib/site";

/** The latest decisions the feed carries, both banks together. */
export const FEED_ITEMS = 20;

const ZONE: Record<Bank, string> = { Fed: "America/New_York", RBI: "Asia/Kolkata" };

/** Escape a text value for XML (& < > " '). */
export function escapeXml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[c]!);
}

/** The instant a calendar date begins in a zone (its 00:00 there), for a decision with no stored time. */
function startOfDayIn(date: string, zone: string): Date {
  const [y, m, d] = date.split("-").map(Number);
  const wall = Date.UTC(y, m - 1, d);
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    hourCycle: "h23",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
  });
  const shown = (at: number): number => {
    const p = Object.fromEntries(fmt.formatToParts(new Date(at)).map((x) => [x.type, Number(x.value)]));
    return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  };
  // The zone's offset at the guess, then again at the corrected instant (a daylight-saving change in between).
  let at = wall - (shown(wall) - wall);
  at = wall - (shown(at) - at);
  return new Date(at);
}

/** RFC 822 (the form `Date.prototype.toUTCString` writes: "Thu, 17 Sep 2026 18:00:00 GMT"). */
export function rfc822(instant: Date): string {
  return instant.toUTCString();
}

/** A decision's publication time: its stored instant, else its date at 00:00 in the bank's zone. */
export function pubDate(m: Pick<PolicyMeetingRow, "bank" | "decision_at" | "decision_date">): string {
  return rfc822(m.decision_at ? new Date(m.decision_at) : startOfDayIn(m.decision_date, ZONE[m.bank as Bank]));
}

/** An item's guid: stable across a change of domain, so readers don't show every item as new again. */
export function guidOf(slug: string): string {
  return `finance-terminal:policy:${slug}`;
}

type Item = { title: string; link: string; guid: string; pubDate: string; description: string };

function itemOf(m: PolicyMeetingRow, votes: readonly PolicyVoteRow[], origin: URL): Item | null {
  const line = decisionLine(m);
  if (line === null) return null;
  const vote = rateVoteLine(m, votes);
  const when = `Decided ${formatDate(m.decision_date)}.`;
  return {
    title: `${m.bank}: ${line}`,
    link: new URL(`${meetingHref(m.bank as Bank, m.decision_date)}?tab=changes`, origin).toString(),
    guid: guidOf(m.slug),
    pubDate: pubDate(m),
    description: vote ? `${when} ${vote}` : when,
  };
}

/** The feed's items: the latest held rate decisions of both banks, newest first, at most FEED_ITEMS. */
export function feedItems(decisions: readonly PolicyMeetingRow[], votes: readonly PolicyVoteRow[], origin: URL): Item[] {
  const bySlug = new Map<string, PolicyVoteRow[]>();
  for (const v of votes) bySlug.set(v.meeting_slug, [...(bySlug.get(v.meeting_slug) ?? []), v]);
  const items: Item[] = [];
  const newest = [...decisions].sort((a, b) => (a.decision_date === b.decision_date ? b.slug.localeCompare(a.slug) : a.decision_date < b.decision_date ? 1 : -1));
  for (const m of newest) {
    if (items.length >= FEED_ITEMS) break;
    const item = itemOf(m, bySlug.get(m.slug) ?? [], origin);
    if (item) items.push(item);
  }
  return items;
}

/** The RSS 2.0 document for these decisions. `builtAt` is when the rows were read. */
export function buildRss(decisions: readonly PolicyMeetingRow[], votes: readonly PolicyVoteRow[], origin: URL, builtAt: Date): string {
  const home = new URL("/", origin).toString();
  const self = new URL("/feed.xml", origin).toString();
  const items = feedItems(decisions, votes, origin)
    .map(
      (i) =>
        `    <item>
      <title>${escapeXml(i.title)}</title>
      <link>${escapeXml(i.link)}</link>
      <guid isPermaLink="false">${escapeXml(i.guid)}</guid>
      <pubDate>${i.pubDate}</pubDate>
      <description>${escapeXml(i.description)}</description>
    </item>`,
    )
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>${escapeXml(SITE_NAME)}</title>
    <link>${escapeXml(home)}</link>
    <description>${escapeXml(SITE_TAGLINE)}</description>
    <language>en</language>
    <lastBuildDate>${rfc822(builtAt)}</lastBuildDate>
    <atom:link href="${escapeXml(self)}" rel="self" type="application/rss+xml" />
${items}
  </channel>
</rss>
`;
}
