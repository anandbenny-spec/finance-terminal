// The labelling page's rules (Prompt 01 E1; Architecture 03 §1.9).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import rawItems from "@/lib/labelling/items.json";
import {
  choices,
  decisionText,
  exportPayload,
  loadLabels,
  memberText,
  nextUnlabelled,
  parseItems,
  storageKey,
  storeLabels,
  validLabel,
  type Item,
  type ItemsFile,
} from "@/lib/labelling/labelling";
import type { DocumentChunkRow } from "@/lib/queries/api";

const fed: Item = { id: "policy_hawk_dove:fomc-2019-07-31", bank: "Fed", meeting: "fomc-2019-07-31", decision_date: "2019-07-31", doc_type: "fomc_statement", phase: "easing" };
const rbi: Item = { id: "policy_stance:mpc-2019-06-06", bank: "RBI", meeting: "mpc-2019-06-06", decision_date: "2019-06-06", doc_type: "mpc_resolution", phase: "easing" };
const file: ItemsFile = {
  rule_version: 1,
  chosen_from: "dev",
  sets: { policy_hawk_dove: [fed], policy_stance: [rbi], policy_member_hawk_dove: [] },
  rbi_stance_drafts: { [rbi.id]: { label: "accommodative" } },
};

describe("the items file", () => {
  it("is the same file as py/config/eval/sets/labelling_items.json", () => {
    const py = readFileSync(new URL("../../../py/config/eval/sets/labelling_items.json", import.meta.url), "utf8");
    expect(readFileSync(new URL("./items.json", import.meta.url), "utf8")).toBe(py);
  });

  it("parses, and refuses a malformed one", () => {
    expect(parseItems(rawItems)).not.toBeNull();
    expect(parseItems(file)?.sets.policy_stance).toHaveLength(1);
    expect(parseItems(null)).toBeNull();
    expect(parseItems({ rule_version: 1, sets: { policy_hawk_dove: [] } })).toBeNull();
    expect(parseItems({ ...file, sets: { ...file.sets, policy_hawk_dove: [{ ...fed, bank: "ECB" }] } })).toBeNull();
    expect(parseItems({ ...file, sets: { ...file.sets, policy_member_hawk_dove: [{ ...rbi, doc_type: "mpc_minutes" }] } })).toBeNull();
  });
});

describe("labels", () => {
  it("offers the set's choices: scores, the Fed's guidance direction, RBI's stance list", () => {
    expect(choices("policy_hawk_dove", fed)).toEqual(["-2", "-1", "0", "+1", "+2"]);
    expect(choices("policy_stance", fed)).toEqual(["easing bias", "neutral", "tightening bias"]);
    expect(choices("policy_stance", rbi)).toContain("withdrawal of accommodation");
  });

  it("needs a valid choice and a one-line reason", () => {
    expect(validLabel("policy_hawk_dove", fed, "-1", "25 bps cut")).toBe(true);
    expect(validLabel("policy_hawk_dove", fed, "-3", "x")).toBe(false);
    expect(validLabel("policy_hawk_dove", fed, "-1", "  ")).toBe(false);
    expect(validLabel("policy_hawk_dove", fed, "-1", "two\nlines")).toBe(false);
    expect(validLabel("policy_hawk_dove", fed, "-1", "x".repeat(201))).toBe(false);
    expect(validLabel("policy_stance", rbi, "easing bias", "x")).toBe(false);
  });

  it("keeps labels per set and rule version, and survives a broken store", () => {
    const store = new Map<string, string>();
    const s = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    const key = storageKey("policy_hawk_dove", 1);
    expect(key).toBe("ft-labels:policy_hawk_dove:v1");
    expect(storeLabels(s, key, { [fed.id]: { label: "-1", reason: "cut", saved_at: "t" } })).toBe(true);
    expect(loadLabels(s, key)[fed.id].label).toBe("-1");
    expect(loadLabels({ getItem: () => "not json" }, key)).toEqual({});
    expect(loadLabels({ getItem: () => { throw new Error("blocked"); } }, key)).toEqual({});
    expect(storeLabels({ setItem: () => { throw new Error("full"); } }, key, {})).toBe(false);
    expect(storeLabels(undefined, key, {})).toBe(false);
  });

  it("exports every item in order, the unlabelled ones as null, and never Claude's draft", () => {
    const out = exportPayload(file, "policy_stance", {}, "2026-11-01T00:00:00Z");
    expect(out).toMatchObject({ set: "policy_stance", rule_version: 1, complete: false, labels: [{ id: rbi.id, label: null, reason: null }] });
    expect(JSON.stringify(out)).not.toContain("accommodative");
    const done = exportPayload(file, "policy_hawk_dove", { [fed.id]: { label: "-1", reason: "cut", saved_at: "t" } }, "t");
    expect(done.complete).toBe(true);
    expect(done.labels[0]).toEqual({ id: fed.id, bank: "Fed", meeting: fed.meeting, label: "-1", reason: "cut" });
  });

  it("opens at the first unlabelled item", () => {
    const b = { ...fed, id: "b" };
    expect(nextUnlabelled([fed, b], {})).toBe(0);
    expect(nextUnlabelled([fed, b], { [fed.id]: { label: "0", reason: "x", saved_at: "t" } })).toBe(1);
    expect(nextUnlabelled([fed, b], { [fed.id]: { label: "0", reason: "x", saved_at: "t" }, b: { label: "0", reason: "x", saved_at: "t" } })).toBe(1);
    expect(nextUnlabelled([], {})).toBe(0);
  });
});

describe("the highlighted paragraph", () => {
  const text = "Recent indicators point to moderate growth.\n\nIn light of these developments, the Committee decided to lower the target range to 2 to 2-1/4 percent.\n\nVoting for the action were …";

  it("is the decision document's paragraph holding the rate setting's span", () => {
    const at = text.indexOf("target range");
    const t = decisionText("Fed", 7, text, [{ instrument: "fed_target_upper", document_id: 7, char_start: at }]);
    expect(t.highlight?.n).toBe(2);
    expect(decisionText("Fed", 7, text, [{ instrument: "fed_target_upper", document_id: 8, char_start: at }]).highlight).toBeNull();
    expect(decisionText("RBI", 7, text, [{ instrument: "fed_target_upper", document_id: 7, char_start: at }]).highlight).toBeNull();
  });

  it("is a member's vote paragraph, inside their own statement only", () => {
    const parts = ["Minutes opening.\n\n", "I see inflation easing.\n\n", "I vote to reduce the repo rate by 25 basis points.\n\n", "Statement by the next member."];
    let at = 0;
    const chunks: DocumentChunkRow[] = parts.map((body, i) => {
      const c = { document_id: 1, chunk_no: i, char_start: at, char_end: at + body.length, section: null, speaker: i === 1 || i === 2 ? "Dr. A" : i === 3 ? "Dr. B" : null, body };
      at += body.length;
      return c;
    });
    const full = parts.join("");
    const t = memberText(full, chunks, "Dr. A");
    expect(t?.text).toBe("I see inflation easing.\n\nI vote to reduce the repo rate by 25 basis points.\n\n");
    expect(t?.highlight?.text).toContain("I vote to reduce");
    expect(memberText(full, chunks, "Dr. C")).toBeNull();
  });
});
