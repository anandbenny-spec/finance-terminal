// The sidebar (C2, #147; Design 01 §1.11): live pages only, the admin group for the admin only, the right item marked
// for nested routes, and section links under Fed vs RBI that each have a live target.
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { POLICY_SECTION_LIST } from "@/lib/policy/sections";
import { activeHref, NAV, visibleGroups, type NavGroup } from "./nav";

const FED_VS_RBI_SECTIONS = [
  { href: "/policy#where-rates-stand", label: "Where rates stand" },
  { href: "/policy#latest-decisions", label: "Latest decisions" },
  { href: "/policy#history", label: "History" },
  { href: "/policy/fed/latest?tab=changes", label: "FOMC: what changed" },
  { href: "/policy/rbi/latest?tab=changes", label: "MPC: what changed" },
];

/** Every link a visitor or the admin can click in the menu: the items and their section links. */
const allLinks = NAV.flatMap((g) => g.items.flatMap((i) => [i.href, ...(i.sections ?? []).map((s) => s.href)]));

describe("sidebar", () => {
  it("lists Macro and policy › Fed vs RBI for visitors, and adds the admin group for the admin", () => {
    expect(visibleGroups(false).map((g) => [g.heading, g.items.map((i) => i.label)])).toEqual([
      ["Overview", ["Start here"]],
      ["Macro and policy", ["Fed vs RBI"]],
      ["About", ["About and privacy"]],
    ]);
    expect(visibleGroups(true).map((g) => [g.heading, g.items.map((i) => i.label)])).toEqual([
      ["Overview", ["Start here"]],
      ["Macro and policy", ["Fed vs RBI"]],
      ["About", ["About and privacy"]],
      ["Admin", ["Admin", "Health", "Labelling"]],
    ]);
  });

  it("gives Fed vs RBI its five section links, in this order, and no other item any", () => {
    const items = NAV.flatMap((g) => g.items);
    expect(items.find((i) => i.href === "/policy")?.sections).toEqual(FED_VS_RBI_SECTIONS);
    expect(items.filter((i) => i.href !== "/policy").every((i) => i.sections === undefined)).toBe(true);
    // the admin sees the same section links as a visitor
    const policyItems = (isAdmin: boolean) => visibleGroups(isAdmin).flatMap((g) => g.items).find((i) => i.href === "/policy");
    expect(policyItems(true)?.sections).toEqual(policyItems(false)?.sections);
  });

  it("keeps the groups in Design 01 §4.1's order, so later jobs slot theirs in by it", () => {
    const order = ["Overview", "Macro and policy", "Company research", "Workspace", "About", "Admin"];
    const headings = NAV.map((g) => g.heading);
    expect(headings.map((h) => order.indexOf(h))).toEqual([...headings.map((h) => order.indexOf(h))].sort((a, b) => a - b));
    expect(headings.every((h) => order.includes(h))).toBe(true);
  });

  it("names no page that isn't built (no 'coming soon')", () => {
    expect(allLinks).toEqual([
      "/",
      "/policy",
      ...FED_VS_RBI_SECTIONS.map((s) => s.href),
      "/about",
      "/admin",
      "/admin/health",
      "/admin/labelling",
    ]);
  });

  it("points every item at a live page, the front page included (#152)", () => {
    for (const { href } of NAV.flatMap((g) => g.items)) {
      const file = fileURLToPath(new URL(`../../app${href === "/" ? "" : href}/page.tsx`, import.meta.url));
      expect(existsSync(file), `${file} must exist`).toBe(true);
    }
  });

  it("points every section link at a live target: a section the page has, or a /latest route that exists", () => {
    const sectionIds = POLICY_SECTION_LIST.map((s) => s.id);
    const subLinks = NAV.flatMap((g) => g.items.flatMap((i) => i.sections ?? []));
    for (const { href } of subLinks) {
      const [path, hash] = href.split("#");
      if (hash !== undefined) {
        expect(path).toBe("/policy");
        expect(sectionIds).toContain(hash);
        continue;
      }
      const latest = /^\/policy\/(fed|rbi)\/latest\?tab=changes$/.exec(href);
      expect(latest, `${href} is neither a /policy section nor a /latest route`).not.toBeNull();
      const file = fileURLToPath(new URL(`../../app/policy/${latest![1]}/latest/route.ts`, import.meta.url));
      expect(existsSync(file), `${file} must exist`).toBe(true);
    }
  });

  it("marks the item a path belongs to, the most specific one first", () => {
    expect(activeHref("/policy")).toBe("/policy");
    expect(activeHref("/policy/fed/2026-10-28")).toBe("/policy");
    expect(activeHref("/policy/fed/2026-09-16")).toBe("/policy");
    expect(activeHref("/policy/rbi/latest")).toBe("/policy");
    expect(activeHref("/admin/health")).toBe("/admin/health");
    expect(activeHref("/admin")).toBe("/admin");
    expect(activeHref("/admin/labelling")).toBe("/admin/labelling");
    expect(activeHref("/about")).toBe("/about");
    expect(activeHref("/policyx")).toBeNull();
    // "Start here" is current on the front page only, never as the parent of every path
    expect(activeHref("/")).toBe("/");
    expect(activeHref("/about")).not.toBe("/");
    expect(activeHref("/nowhere")).toBeNull();
    expect(activeHref(null)).toBeNull();
  });

  it("never marks a section link current: only top-level items match, whatever the path", () => {
    const groups: readonly NavGroup[] = [
      { heading: "Macro and policy", items: [{ href: "/policy", label: "Fed vs RBI", icon: "policy", sections: [{ href: "/policy/deep", label: "Deep" }] }] },
    ];
    expect(activeHref("/policy/deep", groups)).toBe("/policy");
  });
});
