// The sidebar (C2; Design 01 §1.11; Spec 01 §4.1): live pages only, the admin group for the admin only, and the
// right item marked for nested routes.
import { describe, expect, it } from "vitest";
import { activeHref, NAV, visibleGroups } from "./nav";

describe("sidebar", () => {
  it("lists Macro and policy › Fed vs RBI for visitors, and adds the admin group for the admin", () => {
    expect(visibleGroups(false).map((g) => [g.heading, g.items.map((i) => i.label)])).toEqual([["Macro and policy", ["Fed vs RBI"]]]);
    expect(visibleGroups(true).map((g) => g.heading)).toEqual(["Macro and policy", "Admin"]);
  });

  it("names no page that isn't built (no 'coming soon')", () => {
    const hrefs = NAV.flatMap((g) => g.items.map((i) => i.href));
    expect(hrefs).toEqual(["/policy", "/admin", "/admin/health"]);
  });

  it("marks the item a path belongs to, the most specific one first", () => {
    expect(activeHref("/policy")).toBe("/policy");
    expect(activeHref("/policy/fed/2026-10-28")).toBe("/policy");
    expect(activeHref("/admin/health")).toBe("/admin/health");
    expect(activeHref("/admin")).toBe("/admin");
    expect(activeHref("/policyx")).toBeNull();
    expect(activeHref("/")).toBeNull();
    expect(activeHref(null)).toBeNull();
  });
});
