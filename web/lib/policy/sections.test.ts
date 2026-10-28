// The Fed vs RBI page's sections (#147): one list for the page and the menu, so a link can't outlive its section.
import { describe, expect, it } from "vitest";
import { POLICY_SECTIONS, POLICY_SECTION_LIST, policySectionHref } from "./sections";

describe("POLICY_SECTIONS", () => {
  it("names the page's three sections, in page order, with ids that are valid and unique", () => {
    expect(POLICY_SECTION_LIST.map((s) => [s.id, s.title])).toEqual([
      ["where-rates-stand", "Where rates stand"],
      ["latest-decisions", "Latest decisions"],
      ["history", "History"],
    ]);
    const ids = POLICY_SECTION_LIST.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[a-z]+(-[a-z]+)*$/);
  });

  it("lists every section of the record, so none can be left out of the menu", () => {
    expect([...POLICY_SECTION_LIST].sort((a, b) => a.id.localeCompare(b.id))).toEqual(
      Object.values(POLICY_SECTIONS).sort((a, b) => a.id.localeCompare(b.id)),
    );
  });

  it("links a section as /policy#id", () => {
    expect(policySectionHref(POLICY_SECTIONS.history)).toBe("/policy#history");
    expect(policySectionHref(POLICY_SECTIONS.latestDecisions)).toBe("/policy#latest-decisions");
  });
});
