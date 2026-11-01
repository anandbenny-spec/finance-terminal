// The footer's RSS link and the head's feed discovery (#252).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { Footer } from "@/components/shell/Footer";

// next/font only works inside Next's build; the layout needs the font objects to import.
vi.mock("@/app/fonts", () => ({ plexSans: { variable: "" }, plexMono: { variable: "" } }));
const { metadata } = await import("@/app/layout");

describe("RSS discovery", () => {
  it("shows a plain RSS link to /feed.xml after Methodology and limitations", () => {
    const markup = renderToStaticMarkup(<Footer />);
    expect(markup).toContain('href="/feed.xml"');
    expect(markup.indexOf("Methodology and limitations")).toBeLessThan(markup.indexOf(">RSS<"));
  });

  it("advertises the feed in the head's metadata", () => {
    expect(metadata.alternates?.types).toEqual({ "application/rss+xml": [{ url: "/feed.xml", title: "Finance Terminal" }] });
  });
});
