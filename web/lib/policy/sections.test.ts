// The Fed vs RBI page's sections (#147): one list for the page and the menu, so a link can't outlive its section.
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { POLICY_SECTIONS, POLICY_SECTION_LIST, policySectionHref, policySectionLabel } from "./sections";

describe("POLICY_SECTIONS", () => {
  it("names the page's six sections, in page order, with ids that are valid and unique", () => {
    expect(POLICY_SECTION_LIST.map((s) => [s.id, s.title])).toEqual([
      ["where-rates-stand", "Where rates stand"],
      ["latest-decisions", "Latest decisions"],
      ["statement-changes", "How much each statement changed"],
      ["votes-over-time", "How members voted over time"],
      ["language-timeline", "How the wording moved"],
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

  it("is the order /policy draws its sections in, each by its id from this list (#575)", () => {
    const src = readFileSync(join(__dirname, "../../app/policy/page.tsx"), "utf8");
    const keys = ["whereRatesStand", "latestDecisions", "statementChanges", "votesOverTime", "languageTimeline", "history"];
    const at = keys.map((k) => src.search(new RegExp(`<Section (\\{\\.\\.\\.POLICY_SECTIONS\\.${k}\\}|id=\\{POLICY_SECTIONS\\.${k}\\.id\\})`)));
    expect(at.every((i) => i > 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(src).not.toMatch(/<Section id="/); // no section's id is typed out on the page
  });

  it("labels the menu with a short label where the heading is a question, the heading otherwise", () => {
    expect(POLICY_SECTION_LIST.map(policySectionLabel)).toEqual([
      "Where rates stand",
      "Latest decisions",
      "Statement changes",
      "Votes over time",
      "Wording over time",
      "History",
    ]);
  });

  it("links a section as /policy#id", () => {
    expect(policySectionHref(POLICY_SECTIONS.history)).toBe("/policy#history");
    expect(policySectionHref(POLICY_SECTIONS.latestDecisions)).toBe("/policy#latest-decisions");
  });
});
