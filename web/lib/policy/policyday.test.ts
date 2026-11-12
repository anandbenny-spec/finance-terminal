// Policy Day's rules (#90): hidden unless site.policy_day is shown or labelled; which bank shows.
import { describe, expect, it } from "vitest";
import type { FieldPolicyRow } from "@/lib/queries/api";
import { bankParam, pickBank, policyDayHref, policyDayShown, POLICY_DAY_BANKS, POLICY_DAY_FIELD } from "./policyday";

const row = (status: FieldPolicyRow["status"], field_key = POLICY_DAY_FIELD): FieldPolicyRow => ({ field_key, status, label: null });

describe("policyDayShown", () => {
  it("is hidden with no row, no policies, or a hidden row (fails closed)", () => {
    expect(policyDayShown(null)).toBe(false);
    expect(policyDayShown([])).toBe(false);
    expect(policyDayShown([row("hidden")])).toBe(false);
    expect(policyDayShown([row("shown", "ai.doc_summary")])).toBe(false);
  });
  it("shows for a shown or labelled row", () => {
    expect(policyDayShown([row("shown")])).toBe(true);
    expect(policyDayShown([row("labelled")])).toBe(true);
  });
});

describe("which bank", () => {
  it("reads the bank from the path segment and ignores anything else", () => {
    expect(bankParam("fed")).toBe("Fed");
    expect(bankParam("rbi")).toBe("RBI");
    expect(bankParam(["rbi", "fed"])).toBe("RBI");
    expect(bankParam("FED")).toBeNull();
    expect(bankParam(undefined)).toBeNull();
  });
  it("prefers the bank asked for, else the newer decision, the Fed on a tie or when neither is known", () => {
    expect(pickBank("RBI", "2026-10-29", null)).toBe("RBI");
    expect(pickBank(null, "2026-10-29", "2026-10-07")).toBe("Fed");
    expect(pickBank(null, "2026-09-16", "2026-10-07")).toBe("RBI");
    expect(pickBank(null, "2026-10-07", "2026-10-07")).toBe("Fed");
    expect(pickBank(null, null, "2026-10-07")).toBe("RBI");
    expect(pickBank(null, null, null)).toBe("Fed");
  });
  it("links each bank by its own path, so the pages can be prerendered (12 Nov 2026: no query string)", () => {
    expect(policyDayHref("Fed")).toBe("/workspace/policy-day/fed");
    expect(policyDayHref("RBI")).toBe("/workspace/policy-day/rbi");
    expect(policyDayHref("Fed")).not.toContain("?");
  });
  it("names the two segments the build prerenders, each one a bank the page accepts", () => {
    expect([...POLICY_DAY_BANKS]).toEqual(["fed", "rbi"]);
    expect(POLICY_DAY_BANKS.map((b) => bankParam(b))).toEqual(["Fed", "RBI"]);
    expect(POLICY_DAY_BANKS.map((b) => policyDayHref(bankParam(b)!))).toEqual(["/workspace/policy-day/fed", "/workspace/policy-day/rbi"]);
  });
});
