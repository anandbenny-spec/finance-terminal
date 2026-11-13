// The language timeline's cell model and grid (#509): the gate, a row's cell from a meeting's pairs (one match, none,
// several), the marks (the pair's own words, the new ones underlined), and a bank's grid with each kind of gap.
import { describe, expect, it } from "vitest";
import type { RedlinePair } from "@/lib/policy/redline";
import {
  buildBankTimeline,
  cellFor,
  cellsFor,
  gapNote,
  languageTimelineShown,
  LANGUAGE_TIMELINE_FIELD,
  sentenceText,
  type DiffPairs,
  type RowDef,
} from "@/lib/policy/timeline";
import { TIMELINE_ROWS } from "@/lib/policy/timeline-rows";

const field = (status: "shown" | "labelled" | "hidden") => ({ field_key: LANGUAGE_TIMELINE_FIELD, status, label: null });

describe("languageTimelineShown", () => {
  it("is hidden with no row: the database fails closed, and so does the page", () => {
    expect(LANGUAGE_TIMELINE_FIELD).toBe("site.language_timeline");
    expect(languageTimelineShown(null)).toBe(false);
    expect(languageTimelineShown([])).toBe(false);
    expect(languageTimelineShown([{ field_key: "site.votes_over_time", status: "shown", label: null }])).toBe(false);
    expect(languageTimelineShown([field("hidden")])).toBe(false);
    expect(languageTimelineShown([field("shown")])).toBe(true);
    expect(languageTimelineShown([field("labelled")])).toBe(true);
  });
});

const ROW: RowDef = { key: "x", label: "X", tiers: [{ topic: "T", all: [/alpha/i] }] };
const FALLBACK: RowDef = { key: "y", label: "Y", tiers: [{ topic: "T", all: [/alpha/i] }, { topic: "U", all: [/beta/i] }] };
const same = (text: string, topic = "T", newPara: number | null = 1): RedlinePair => ({ kind: "same", text, topic, newPara, oldPara: newPara });
const added = (text: string, topic = "T", newPara: number | null = 2): RedlinePair => ({ kind: "added", text, topic, newPara, oldPara: null });
const removed = (text: string, topic = "T"): RedlinePair => ({ kind: "removed", text, topic, newPara: null, oldPara: 3 });
const changed = (ops: { op: "eq" | "del" | "ins"; text: string }[], topic = "T", newPara: number | null = 4): RedlinePair => ({ kind: "changed", ops, topic, newPara, oldPara: 4 });

describe("cellFor", () => {
  it("is the one matching sentence, with its paragraph, unchanged when the pair is the same", () => {
    expect(cellFor(ROW, [same("Beta."), same("Alpha holds.", "T", 7)])).toEqual({ kind: "sentence", parts: [{ op: "eq", text: "Alpha holds." }], how: "same", para: 7 });
  });

  it("marks a changed sentence's new words and leaves the deleted ones out (the meeting's own words)", () => {
    const ops = [
      { op: "eq" as const, text: "Alpha is " },
      { op: "del" as const, text: "old" },
      { op: "ins" as const, text: "new" },
      { op: "eq" as const, text: " today." },
    ];
    const cell = cellFor(ROW, [changed(ops)]);
    expect(cell).toEqual({
      kind: "sentence",
      parts: [{ op: "eq", text: "Alpha is " }, { op: "ins", text: "new" }, { op: "eq", text: " today." }],
      how: "changed",
      para: 4,
    });
    if (cell.kind === "sentence") {
      expect(sentenceText(cell)).toBe("Alpha is new today.");
      // The marks are exactly the pair's inserted words.
      expect(cell.parts.filter((p) => p.op === "ins").map((p) => p.text)).toEqual(ops.filter((o) => o.op === "ins").map((o) => o.text));
    }
  });

  it("calls a 'changed' pair with nothing inserted unchanged", () => {
    const cell = cellFor(ROW, [changed([{ op: "eq", text: "Alpha " }, { op: "del", text: "was" }, { op: "eq", text: "." }])]);
    expect(cell).toMatchObject({ kind: "sentence", how: "same" });
  });

  it("marks a sentence with no earlier counterpart wholly new", () => {
    expect(cellFor(ROW, [added("Alpha arrives.")])).toEqual({ kind: "sentence", parts: [{ op: "ins", text: "Alpha arrives." }], how: "added", para: 2 });
  });

  it("is a gap when nothing matches, or the topic differs", () => {
    expect(cellFor(ROW, [same("Beta.")])).toEqual({ kind: "gap", reason: "none", matched: 0 });
    expect(cellFor(ROW, [same("Alpha.", "Other")])).toEqual({ kind: "gap", reason: "none", matched: 0 });
    expect(cellFor(ROW, [])).toEqual({ kind: "gap", reason: "none", matched: 0 });
  });

  it("is a gap when more than one sentence matches, counting each, never the first", () => {
    expect(cellFor(ROW, [same("Alpha one."), added("Alpha two."), changed([{ op: "eq", text: "Alpha three." }])])).toEqual({ kind: "gap", reason: "several", matched: 3 });
  });

  it("doesn't count a sentence the meeting dropped", () => {
    expect(cellFor(ROW, [removed("Alpha was here."), same("Alpha is here.")])).toMatchObject({ kind: "sentence" });
    expect(cellFor(ROW, [removed("Alpha was here.")])).toEqual({ kind: "gap", reason: "none", matched: 0 });
  });

  it("matches on the meeting's new words, not the words it deleted", () => {
    const ops = [{ op: "del" as const, text: "Alpha " }, { op: "ins" as const, text: "Gamma " }, { op: "eq" as const, text: "stays." }];
    expect(cellFor(ROW, [changed(ops)])).toEqual({ kind: "gap", reason: "none", matched: 0 });
  });

  it("tries the next tier only when the one before found nothing", () => {
    expect(cellFor(FALLBACK, [same("Beta.", "U")])).toMatchObject({ kind: "sentence" });
    expect(cellFor(FALLBACK, [same("Alpha.", "T"), same("Beta.", "U")])).toMatchObject({ kind: "sentence", parts: [{ text: "Alpha." }] });
    expect(cellFor(FALLBACK, [same("Alpha."), same("Alpha again."), same("Beta.", "U")])).toEqual({ kind: "gap", reason: "several", matched: 2 });
  });

  it("holds a sentence's text and nothing of the pair: no ops, no old words, no topic", () => {
    const cell = cellFor(ROW, [changed([{ op: "eq", text: "Alpha " }, { op: "del", text: "SECRET-OLD" }, { op: "ins", text: "new." }])]);
    expect(JSON.stringify(cell)).not.toContain("SECRET-OLD");
    expect(Object.keys(cell).sort()).toEqual(["how", "kind", "para", "parts"]);
  });
});

describe("cellsFor and gapNote", () => {
  it("keys the cells by the rows' keys", () => {
    expect(Object.keys(cellsFor(TIMELINE_ROWS.RBI, []))).toEqual(["stance", "decision", "inflation"]);
  });

  it("says what each gap is and why", () => {
    expect(gapNote({ kind: "gap", reason: "none", matched: 0 })).toEqual({ title: "No single matching sentence", reason: "None of this meeting's sentences matches." });
    expect(gapNote({ kind: "gap", reason: "several", matched: 2 })).toEqual({ title: "No single matching sentence", reason: "2 sentences match, so none is chosen." });
    expect(gapNote({ kind: "gap", reason: "no_document", matched: 0 }).reason).toMatch(/No decision document is stored/);
    expect(gapNote({ kind: "gap", reason: "no_diff", matched: 0 }).reason).toMatch(/no earlier document/);
    expect(gapNote({ kind: "gap", reason: "unreadable", matched: 0 }).reason).toMatch(/couldn't be read/);
  });
});

describe("buildBankTimeline", () => {
  const meetings = [
    { slug: "mpc-2026-10-07", bank: "RBI" as const, decision_date: "2026-10-07" },
    { slug: "mpc-2026-08-06", bank: "RBI" as const, decision_date: "2026-08-06" },
    { slug: "mpc-2026-06-05", bank: "RBI" as const, decision_date: "2026-06-05" },
    { slug: "mpc-2026-04-09", bank: "RBI" as const, decision_date: "2026-04-09" },
    { slug: "mpc-2026-02-06", bank: "RBI" as const, decision_date: "2026-02-06" },
    { slug: "fomc-2026-09-16", bank: "Fed" as const, decision_date: "2026-09-16" },
  ];
  const doc = (id: number, slug: string, version_no = 1) => ({ id, event_slug: slug, version_no });
  const pairs = (p: RedlinePair[]): DiffPairs["pairs"] => p;
  const diff = (document_id: number, p: RedlinePair[], version: unknown = 2): DiffPairs => ({ document_id, base_document_id: document_id - 1, version, pairs: pairs(p) });
  const stance = same("The MPC decided to remain accommodative.", "Decision and stance");
  const rows = TIMELINE_ROWS.RBI;

  const grid = buildBankTimeline(
    "RBI",
    rows,
    meetings,
    [doc(1, "mpc-2026-10-07"), doc(2, "mpc-2026-08-06"), doc(3, "mpc-2026-06-05"), doc(4, "mpc-2026-04-09"), doc(9, "fomc-2026-09-16")],
    [diff(1, [stance]), diff(2, [stance]), diff(3, [stance], 1), /* 4 has no diff: the first of its series */ diff(9, [stance])],
  );

  it("has one column per meeting of the bank, newest first, and the rows' keys and labels", () => {
    expect(grid.columns.map((c) => c.slug)).toEqual(["mpc-2026-10-07", "mpc-2026-08-06", "mpc-2026-06-05", "mpc-2026-04-09", "mpc-2026-02-06"]);
    expect(grid.rows).toEqual(rows.map((r) => ({ key: r.key, label: r.label })));
    expect(grid.latest).toBe("2026-10-07");
    expect(grid.columns[0].href).toBe("/policy/rbi/2026-10-07/statement");
  });

  it("gives each kind of missing diff its own reason, for every row", () => {
    const reasons = Object.fromEntries(grid.columns.map((c) => [c.slug, Object.values(c.cells).map((x) => (x.kind === "gap" ? x.reason : "sentence"))]));
    expect(reasons["mpc-2026-10-07"]).toEqual(["sentence", "none", "none"]);
    expect(reasons["mpc-2026-06-05"]).toEqual(["unreadable", "unreadable", "unreadable"]);
    expect(reasons["mpc-2026-04-09"]).toEqual(["no_diff", "no_diff", "no_diff"]);
    expect(reasons["mpc-2026-02-06"]).toEqual(["no_document", "no_document", "no_document"]);
  });

  it("uses a meeting's latest document version", () => {
    const g = buildBankTimeline("RBI", rows, [meetings[0]], [doc(1, "mpc-2026-10-07"), doc(5, "mpc-2026-10-07", 2)], [diff(1, [stance]), diff(5, [])]);
    expect(g.columns[0].cells.stance).toEqual({ kind: "gap", reason: "none", matched: 0 });
  });

  it("is empty, with no latest date, when the bank has no decisions", () => {
    const g = buildBankTimeline("Fed", TIMELINE_ROWS.Fed, [], [], []);
    expect(g.columns).toEqual([]);
    expect(g.latest).toBeNull();
  });
});
