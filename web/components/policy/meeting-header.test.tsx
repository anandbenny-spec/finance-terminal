// The meeting page's Previous / Next links (#147): both keep the tab being read, on the Overview and on What changed,
// and drop other query parameters. Rendered to HTML as the server sends them; test dates, not a record of meetings.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OtherMeetings } from "@/components/policy/MeetingView";
import { TABS } from "@/lib/policy/decisions";

// Anchors as [href, text]; next/link writes `class` before `href`, so the attributes are read in any order.
const links = (markup: string) =>
  [...markup.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)].map((m) => [/href="([^"]*)"/.exec(m[1])![1], m[2].replace(/<[^>]+>/g, "")]);
const render = (node: Parameters<typeof renderToStaticMarkup>[0]) => links(renderToStaticMarkup(node));

describe("OtherMeetings", () => {
  const previous = { decision_date: "2026-07-29" };
  const next = { decision_date: "2026-11-04" };

  it("keeps What changed: Previous and Next both open that tab", () => {
    expect(render(<OtherMeetings bank="Fed" tab="changes" previous={previous} next={next} />)).toEqual([
      ["/policy/fed/2026-07-29?tab=changes", "← Previous: 29 Jul 2026"],
      ["/policy/fed/2026-11-04?tab=changes", "Next: 4 Nov 2026 →"],
    ]);
  });

  it("on the Overview, links the bare meeting pages (the Overview is the default tab)", () => {
    expect(render(<OtherMeetings bank="RBI" tab="overview" previous={previous} next={next} />)).toEqual([
      ["/policy/rbi/2026-07-29", "← Previous: 29 Jul 2026"],
      ["/policy/rbi/2026-11-04", "Next: 4 Nov 2026 →"],
    ]);
  });

  it("keeps every tab, and never carries another query parameter (doc=) across", () => {
    for (const t of TABS) {
      const hrefs = render(<OtherMeetings bank="RBI" tab={t.id} previous={previous} next={next} />).map(([href]) => href);
      const want = t.id === "overview" ? "" : `?tab=${t.id}`;
      expect(hrefs).toEqual([`/policy/rbi/2026-07-29${want}`, `/policy/rbi/2026-11-04${want}`]);
      expect(hrefs.join()).not.toContain("doc=");
    }
  });

  it("shows only the side that exists", () => {
    expect(render(<OtherMeetings bank="Fed" tab="changes" previous={null} next={next} />)).toEqual([["/policy/fed/2026-11-04?tab=changes", "Next: 4 Nov 2026 →"]]);
    expect(render(<OtherMeetings bank="Fed" tab="changes" previous={previous} next={null} />)).toEqual([["/policy/fed/2026-07-29?tab=changes", "← Previous: 29 Jul 2026"]]);
    expect(render(<OtherMeetings bank="Fed" tab="changes" previous={null} next={null} />)).toEqual([]);
  });
});
