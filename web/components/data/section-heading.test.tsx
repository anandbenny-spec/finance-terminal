// SectionHeading (#147): the heading carries the id a section link jumps to, and a 64 px scroll margin (the 48 px top
// bar plus 16) so the jump lands the heading just under the sticky top bar.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Section } from "@/components/data/SectionHeading";
import { POLICY_SECTIONS } from "@/lib/policy/sections";

describe("Section", () => {
  it("puts the id on the heading the section is labelled by, with a scroll margin for the top bar", () => {
    const out = renderToStaticMarkup(
      <Section {...POLICY_SECTIONS.history}>
        <p>body</p>
      </Section>,
    );
    expect(out).toContain('aria-labelledby="history"');
    const heading = /<h2 id="history" class="([^"]*)">History/.exec(out);
    expect(heading).not.toBeNull();
    expect(heading![1].split(" ")).toContain("scroll-mt-16");
  });
});
