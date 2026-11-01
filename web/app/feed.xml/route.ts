// /feed.xml (#252): the site's RSS feed of held FOMC and MPC rate decisions, automatic items only. A read failure
// answers 503 with no Cache-Control, never an empty feed; a good answer is cached at the edge for an hour.
import { readFeedDecisions } from "@/lib/queries/feed";
import { buildRss } from "@/lib/rss";
import { siteOrigin } from "@/lib/site";

export async function GET(): Promise<Response> {
  const read = await readFeedDecisions();
  if (!read.ok) {
    return new Response("The feed is unavailable right now.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
  return new Response(buildRss(read.decisions, read.votes, siteOrigin(), new Date(read.readAt)), {
    status: 200,
    headers: {
      "Content-Type": "application/rss+xml; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
