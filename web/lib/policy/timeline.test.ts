// The language timeline's cell model and grid (#509, marks reworked in #530): the gate, the word comparison of two sentences,
// a row's sentence from a meeting's pairs (one match, none, several), the marks against the previous column's sentence (new
// words underlined, removed words struck, Unchanged / Changed / New sentence), and a bank's grid with each kind of gap.
import { describe, expect, it } from "vitest";
import type { Op, RedlinePair } from "@/lib/policy/redline";
import {
  baseSentenceFor,
  buildBankTimeline,
  cellFor,
  cellsFor,
  compareSentences,
  gapNote,
  languageTimelineShown,
  LANGUAGE_TIMELINE_FIELD,
  markCell,
  sentenceText,
  type DiffPairs,
  type RowDef,
  type TimelineCell,
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

const E = (text: string): Op => ({ op: "eq", text });
const D = (text: string): Op => ({ op: "del", text });
const I = (text: string): Op => ({ op: "ins", text });

/** What a comparison must keep: the words, with the hyphen variants folded and no whitespace (whitespace is never compared). */
const squash = (s: string) => s.replace(/[‐‑]/g, "-").replace(/\s+/g, "");
const rebuildNow = (ops: readonly Op[]) => ops.filter((o) => o.op !== "del").map((o) => o.text).join("");
const rebuildBefore = (ops: readonly Op[]) => ops.filter((o) => o.op !== "ins").map((o) => o.text).join("");

describe("compareSentences", () => {
  it("is one eq for identical sentences", () => {
    expect(compareSentences("Alpha holds firm.", "Alpha holds firm.")).toEqual([E("Alpha holds firm.")]);
  });

  it("reads a non-breaking hyphen (U+2011) as a hyphen and shows the current sentence's characters", () => {
    // Dec 2018's statement wrote "2‑1/2" with U+2011; Jan 2019's wrote the plain hyphen.
    expect(compareSentences("to 2-1/4 to 2‑1/2 percent.", "to 2-1/4 to 2-1/2 percent.")).toEqual([E("to 2-1/4 to 2-1/2 percent.")]);
    expect(compareSentences("to 2-1/4 to 2-1/2 percent.", "to 2-1/4 to 2‑1/2 percent.")).toEqual([E("to 2-1/4 to 2‑1/2 percent.")]);
    expect(compareSentences("a ‐ b", "a - b")).toEqual([E("a - b")]);
  });

  it("reads a no-break space (U+00A0, U+202F) and a run of spaces as spaces: whitespace is never compared", () => {
    expect(compareSentences("The rate is 5 per cent.", "The rate is 5 per cent.")).toEqual([E("The rate is 5 per cent.")]);
    expect(compareSentences("The rate is 5 per cent.", "The rate is 5 per cent.")).toEqual([E("The rate is 5 per cent.")]);
    expect(compareSentences("The  rate   is 5 per cent.", "The rate is 5 per cent.")).toEqual([E("The rate is 5 per cent.")]);
  });

  it("shows words that were only removed as struck, with nothing inserted", () => {
    expect(compareSentences("Inflation has made further progress toward the goal.", "Inflation has made progress toward the goal.")).toEqual([
      E("Inflation has made"),
      D(" further"),
      E(" progress toward the goal."),
    ]);
  });

  it("shows words that were only added as inserted, with nothing struck", () => {
    expect(compareSentences("Inflation remains elevated.", "Inflation remains somewhat elevated.")).toEqual([E("Inflation remains"), I(" somewhat"), E(" elevated.")]);
  });

  it("puts each changed stretch as one del run and then one ins run", () => {
    expect(compareSentences("The MPC decided to remain neutral.", "The MPC decided to remain accommodative.")).toEqual([E("The MPC decided to remain"), D(" neutral"), I(" accommodative"), E(".")]);
    expect(compareSentences("a b c d e", "a X c Y e")).toEqual([E("a"), D(" b"), I(" X"), E(" c"), D(" d"), I(" Y"), E(" e")]);
    expect(compareSentences("a b c", "x y z")).toEqual([D("a b c"), I("x y z")]);
  });

  it("keeps '2-1/2', '5.3', '1,000' and \"Committee's\" whole: a changed figure is one struck and one new word", () => {
    expect(compareSentences("a rate of 5.3 per cent", "a rate of 5.4 per cent")).toEqual([E("a rate of"), D(" 5.3"), I(" 5.4"), E(" per cent")]);
    expect(compareSentences("up 1,000 basis points", "up 1,500 basis points")).toEqual([E("up"), D(" 1,000"), I(" 1,500"), E(" basis points")]);
    expect(compareSentences("to 2-1/2 percent", "to 2-3/4 percent")).toEqual([E("to"), D(" 2-1/2"), I(" 2-3/4"), E(" percent")]);
    expect(compareSentences("the Committee's goal", "the Committee's aim")).toEqual([E("the Committee's"), D(" goal"), I(" aim")]);
  });

  it("makes each other mark a word of its own", () => {
    expect(compareSentences("elevated, and rising.", "elevated and rising.")).toEqual([E("elevated"), D(","), E(" and rising.")]);
  });

  it("folds nothing else: not case, not quotes, not dashes", () => {
    expect(compareSentences("Inflation rose.", "inflation rose.")).toEqual([D("Inflation"), I("inflation"), E(" rose.")]);
    expect(compareSentences("the Committee's goal", "the Committee’s goal")).toEqual([E("the"), D(" Committee's"), I(" Committee’s"), E(" goal")]);
    // an en dash is a mark of its own, so "10–2" (three tokens) is not "10-2" (one word)
    expect(compareSentences("votes of 10–2", "votes of 10-2")).toEqual([E("votes of"), D(" 10–2"), I(" 10-2")]);
  });

  it("is the same every time, and breaks a tie by taking the previous sentence's word as removed first", () => {
    expect(compareSentences("a b", "b a")).toEqual(compareSentences("a b", "b a"));
    expect(compareSentences("a b", "b a")).toEqual([D("a"), E("b"), I(" a")]);
    expect(compareSentences("", "")).toEqual([]);
    expect(compareSentences("", "Alpha.")).toEqual([I("Alpha.")]);
  });

  // The two consecutive RBI prints of Oct and Dec 2021 (#530's first failed cell), from the saved resolutions. The stored
  // pair left Dec's sentence unpaired and so marked all of it new; the comparison finds the words they share.
  it("finds the shared words of a reworded RBI inflation sentence (7 Oct → 8 Dec 2021) and marks the figures and months", () => {
    const oct = "7. Headline CPI inflation at 5.3 per cent in August softened for the second consecutive month, declining by one percentage point from the recent peak in May-June 2021.";
    const dec =
      "7. Headline CPI inflation, which has been on a downward trajectory since June 2021, edged up to 4.5 per cent in October from 4.3 per cent in September on account of a spike in vegetable prices – due to crop damage from heavy rainfalls in October in several states, and fuel inflation – driven up by international prices of liquefied petroleum gas and kerosene.";
    const ops = compareSentences(oct, dec);
    expect(ops[0]).toEqual(E("7. Headline CPI inflation"));
    expect(ops.at(-1)).toEqual(E("."));
    expect(ops.filter((o) => o.op === "del").map((o) => o.text)).toEqual([" at 5.3", " August softened for the second consecutive month, declining by one percentage point", " the recent peak", " May-June 2021"]);
    expect(ops.filter((o) => o.op === "ins").map((o) => o.text.slice(0, 40))).toEqual([", which has been on a downward trajector", " October", " 4.3 per cent", " September on account of a spike in vege"]);
    expect(ops.some((o) => o.op === "eq" && o.text.includes("per cent in"))).toBe(true);
    expect(rebuildNow(ops)).toBe(dec);
    expect(squash(rebuildBefore(ops))).toBe(squash(oct));
  });

  // A seeded property test: random sentences edited at random, with whitespace and hyphen variants thrown in. The fixture
  // replica (every sentence of both banks) is checked the same way by the session that built this, but the saved pages
  // can't be in this repository, and the web job has no copy of them (CLAUDE.md §4).
  describe("property: any two sentences", () => {
    const WORDS = ["inflation", "Committee's", "2-1/2", "5.3", "1,000", "percent", "elevated", "rate", "stance", "progress", "the", "target", "range", "labor", "market", "objective", "remain", "somewhat"];
    const rng = (seed: number) => () => {
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };

    function sentenceFrom(next: () => number) {
      const pick = <T,>(xs: readonly T[]) => xs[Math.floor(next() * xs.length)];
      const prev = Array.from({ length: 3 + Math.floor(next() * 25) }, () => pick(WORDS));
      const cur = [...prev];
      const edits = Math.floor(next() * 5);
      for (let e = 0; e < edits; e++) {
        const at = Math.floor(next() * (cur.length + 1));
        const kind = next();
        if (kind < 0.34 && cur.length > 1) cur.splice(Math.min(at, cur.length - 1), 1);
        else if (kind < 0.67) cur.splice(at, 0, `new${Math.floor(next() * 4)}`);
        else if (cur.length > 0) cur[Math.min(at, cur.length - 1)] = `swap${Math.floor(next() * 4)}`;
      }
      // whitespace and hyphen variants, on both sides, that must never count as a change
      const space = () => pick([" ", " ", " ", "  ", " ", " "]);
      const join = (ws: string[], variants: boolean) => ws.map((w, i) => (variants && next() < 0.3 ? w.replace(/-/g, "‑") : w) + (i < ws.length - 1 ? space() : "")).join("");
      return { previous: join(prev, true), current: join(cur, true), previousWords: prev, currentWords: cur, edits };
    }

    it("rebuilds the current sentence from its eq and ins, and the previous one (words only) from its eq and del", () => {
      const next = rng(530);
      for (let n = 0; n < 400; n++) {
        const { previous, current } = sentenceFrom(next);
        const ops = compareSentences(previous, current);
        expect(rebuildNow(ops), `${n}: ${previous} | ${current}`).toBe(current);
        expect(squash(rebuildBefore(ops)), `${n}: ${previous} | ${current}`).toBe(squash(previous));
      }
    });

    it("alternates eq and changed stretches, a del before an ins, and never an empty op", () => {
      const next = rng(7530);
      for (let n = 0; n < 400; n++) {
        const { previous, current } = sentenceFrom(next);
        const ops = compareSentences(previous, current);
        for (const [k, o] of ops.entries()) {
          expect(o.text.length).toBeGreaterThan(0);
          if (k > 0) {
            expect(o.op === ops[k - 1].op, `${n}: two ${o.op} runs in a row`).toBe(false);
            expect(ops[k - 1].op === "ins" && o.op === "del", `${n}: an ins before a del`).toBe(false);
          }
        }
      }
    });

    it("keeps every word that no edit touched, and is one eq when only whitespace and hyphens differ", () => {
      const next = rng(99);
      for (let n = 0; n < 400; n++) {
        const { previous, current, previousWords, currentWords, edits } = sentenceFrom(next);
        const ops = compareSentences(previous, current);
        // An edit touches at most one of the previous sentence's words, and the comparison keeps the longest run of shared words.
        const kept = ops.filter((o) => o.op === "eq").flatMap((o) => o.text.split(/\s+/).filter(Boolean)).length;
        expect(kept, `${n}: ${previous} | ${current}`).toBeGreaterThanOrEqual(previousWords.length - edits);
        if (JSON.stringify(previousWords) === JSON.stringify(currentWords)) expect(ops, `${n}`).toEqual([E(current)]);
      }
    });
  });
});

// ------------------------------------------------------------------------------------------------ a row's sentence

const ROW: RowDef = { key: "x", label: "X", tiers: [{ topic: "T", all: [/alpha/i] }] };
const FALLBACK: RowDef = { key: "y", label: "Y", tiers: [{ topic: "T", all: [/alpha/i] }, { topic: "U", all: [/beta/i] }] };
const same = (text: string, topic = "T", newPara: number | null = 1): RedlinePair => ({ kind: "same", text, topic, newPara, oldPara: newPara });
const added = (text: string, topic = "T", newPara: number | null = 2): RedlinePair => ({ kind: "added", text, topic, newPara, oldPara: null });
const removed = (text: string, topic = "T"): RedlinePair => ({ kind: "removed", text, topic, newPara: null, oldPara: 3 });
const changed = (ops: Op[], topic = "T", newPara: number | null = 4): RedlinePair => ({ kind: "changed", ops, topic, newPara, oldPara: 4 });

describe("cellFor", () => {
  it("is the one matching sentence, with its paragraph, not yet marked", () => {
    expect(cellFor(ROW, [same("Beta."), same("Alpha holds.", "T", 7)])).toEqual({ kind: "found", text: "Alpha holds.", para: 7 });
  });

  it("takes a changed pair's sentence from the meeting's own words and leaves the deleted ones out", () => {
    const cell = cellFor(ROW, [changed([E("Alpha is "), D("old"), I("new"), E(" today.")])]);
    expect(cell).toEqual({ kind: "found", text: "Alpha is new today.", para: 4 });
  });

  it("takes a changed pair that only removed words as the words that remain", () => {
    expect(cellFor(ROW, [changed([E("Alpha "), D("was"), E(".")])])).toEqual({ kind: "found", text: "Alpha .", para: 4 });
  });

  it("finds a sentence the differ found no counterpart for like any other", () => {
    expect(cellFor(ROW, [added("Alpha arrives.")])).toEqual({ kind: "found", text: "Alpha arrives.", para: 2 });
  });

  it("is a gap when nothing matches, or the topic differs", () => {
    expect(cellFor(ROW, [same("Beta.")])).toEqual({ kind: "gap", reason: "none", matched: 0 });
    expect(cellFor(ROW, [same("Alpha.", "Other")])).toEqual({ kind: "gap", reason: "none", matched: 0 });
    expect(cellFor(ROW, [])).toEqual({ kind: "gap", reason: "none", matched: 0 });
  });

  it("is a gap when more than one sentence matches, counting each, never the first", () => {
    expect(cellFor(ROW, [same("Alpha one."), added("Alpha two."), changed([E("Alpha three.")])])).toEqual({ kind: "gap", reason: "several", matched: 3 });
  });

  it("doesn't count a sentence the meeting dropped", () => {
    expect(cellFor(ROW, [removed("Alpha was here."), same("Alpha is here.")])).toMatchObject({ kind: "found" });
    expect(cellFor(ROW, [removed("Alpha was here.")])).toEqual({ kind: "gap", reason: "none", matched: 0 });
  });

  it("matches on the meeting's new words, not the words it deleted", () => {
    expect(cellFor(ROW, [changed([D("Alpha "), I("Gamma "), E("stays.")])])).toEqual({ kind: "gap", reason: "none", matched: 0 });
  });

  it("tries the next tier only when the one before found nothing", () => {
    expect(cellFor(FALLBACK, [same("Beta.", "U")])).toMatchObject({ kind: "found" });
    expect(cellFor(FALLBACK, [same("Alpha.", "T"), same("Beta.", "U")])).toMatchObject({ kind: "found", text: "Alpha." });
    expect(cellFor(FALLBACK, [same("Alpha."), same("Alpha again."), same("Beta.", "U")])).toEqual({ kind: "gap", reason: "several", matched: 2 });
  });

  it("holds a sentence's text and nothing of the pair: no ops, no old words, no topic", () => {
    const cell = cellFor(ROW, [changed([E("Alpha "), D("SECRET-OLD"), I("new.")])]);
    expect(JSON.stringify(cell)).not.toContain("SECRET-OLD");
    expect(Object.keys(cell).sort()).toEqual(["kind", "para", "text"]);
  });
});

describe("baseSentenceFor", () => {
  it("is the row's one sentence on the base side: a same or removed pair's text, a changed pair's eq and del words", () => {
    expect(baseSentenceFor(ROW, [same("Beta."), same("Alpha holds.")])).toBe("Alpha holds.");
    expect(baseSentenceFor(ROW, [removed("Alpha was here.")])).toBe("Alpha was here.");
    expect(baseSentenceFor(ROW, [changed([E("Alpha is "), D("old"), I("new"), E(" today.")])])).toBe("Alpha is old today.");
  });

  it("ignores a sentence the base document didn't have, and a topic the row doesn't read", () => {
    expect(baseSentenceFor(ROW, [added("Alpha arrives.")])).toBeNull();
    expect(baseSentenceFor(ROW, [same("Alpha.", "Other")])).toBeNull();
  });

  it("is null unless exactly one sentence matches", () => {
    expect(baseSentenceFor(ROW, [])).toBeNull();
    expect(baseSentenceFor(ROW, [same("Alpha one."), removed("Alpha two.")])).toBeNull();
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

// ------------------------------------------------------------------------------------------------ the marks

describe("markCell", () => {
  const found = { kind: "found" as const, text: "Alpha holds firm.", para: 5 };

  it("is New sentence, every word underlined, when there is no previous sentence", () => {
    expect(markCell(found, null)).toEqual({ kind: "sentence", parts: [I("Alpha holds firm.")], how: "new", para: 5 });
  });

  it("is Unchanged for the same words, whatever hyphen or space they were written with", () => {
    expect(markCell(found, "Alpha holds firm.")).toEqual({ kind: "sentence", parts: [E("Alpha holds firm.")], how: "same", para: 5 });
    expect(markCell({ ...found, text: "Alpha holds 2-1/2." }, "Alpha holds 2‑1/2.")).toMatchObject({ how: "same", parts: [E("Alpha holds 2-1/2.")] });
  });

  it("is Changed for any word new or removed, a removal alone included", () => {
    expect(markCell(found, "Alpha holds very firm.")).toEqual({ kind: "sentence", parts: [E("Alpha holds"), D(" very"), E(" firm.")], how: "changed", para: 5 });
    expect(markCell(found, "Alpha firm.")).toMatchObject({ how: "changed", parts: [E("Alpha"), I(" holds"), E(" firm.")] });
  });

  it("holds a sentence's text and marks and nothing else", () => {
    expect(Object.keys(markCell(found, "Alpha holds very firm.")).sort()).toEqual(["how", "kind", "para", "parts"]);
    expect(sentenceText(markCell(found, "Alpha holds very firm."))).toBe("Alpha holds firm.");
  });
});

// ------------------------------------------------------------------------------------------------ the grid

/** A raw stored pair, as `parsePairs` reads it (snake_case spans), so a cell's paragraph comes through. */
const raw = (kind: "same" | "added" | "removed", topic: string, text: string, newPara: number | null = 1) => ({
  kind, topic, text, new_para: newPara === null ? [] : [newPara], old_para: kind === "added" ? [] : [1],
});
const rawChanged = (topic: string, ops: Op[], newPara = 1) => ({ kind: "changed", topic, ops, new_para: [newPara], old_para: [1] });

type Col = { date: string; pairs: unknown[] | "no_diff" | "no_document" | "unreadable" };

/** A bank's grid for one row rule from columns given newest first. */
function gridOf(row: RowDef, cols: Col[], bank: "RBI" | "Fed" = "RBI") {
  const prefix = bank === "Fed" ? "fomc" : "mpc";
  const decisions = cols.map((c) => ({ slug: `${prefix}-${c.date}`, bank, decision_date: c.date }));
  const documents = cols.flatMap((c, i) => (c.pairs === "no_document" ? [] : [{ id: i + 1, event_slug: `${prefix}-${c.date}`, version_no: 1 }]));
  const diffs = cols.flatMap((c, i): DiffPairs[] =>
    c.pairs === "no_document" || c.pairs === "no_diff" ? [] : [{ document_id: i + 1, base_document_id: 100 + i, version: c.pairs === "unreadable" ? 1 : 2, pairs: c.pairs === "unreadable" ? [] : c.pairs }],
  );
  const grid = buildBankTimeline(bank, [row], decisions, documents, diffs);
  return { grid, cells: grid.columns.map((c) => c.cells[row.key]) };
}

const sentenceCell = (cell: TimelineCell) => {
  if (cell.kind !== "sentence") throw new Error(`expected a sentence, got a ${cell.reason} gap`);
  return cell;
};

describe("buildBankTimeline: the marks", () => {
  it("marks each cell against the previous column's sentence, not against its stored pair", () => {
    // Oct's stored pair is 'added' (the differ found no counterpart) and Aug's 'changed' with nothing inserted: neither is
    // used. Oct is compared with Aug's sentence, Aug with Jun's, and Jun, the oldest, with the base side of its own pair.
    const { cells } = gridOf(ROW, [
      { date: "2026-10-07", pairs: [raw("added", "T", "Alpha stays accommodative.")] },
      { date: "2026-08-05", pairs: [rawChanged("T", [E("Alpha stays "), D("very "), E("neutral.")])] },
      { date: "2026-06-05", pairs: [raw("same", "T", "Alpha stays very neutral.")] },
    ]);
    expect(cells[2]).toEqual({ kind: "sentence", parts: [E("Alpha stays very neutral.")], how: "same", para: 1 });
    expect(cells[1]).toEqual({ kind: "sentence", parts: [E("Alpha stays"), D(" very"), E(" neutral.")], how: "changed", para: 1 });
    expect(cells[0]).toEqual({ kind: "sentence", parts: [E("Alpha stays"), D(" neutral"), I(" accommodative"), E(".")], how: "changed", para: 1 });
  });

  it("says Unchanged only for the same words, and Changed where words were only removed", () => {
    const { cells } = gridOf(ROW, [
      { date: "2026-10-07", pairs: [raw("same", "T", "Alpha has made progress toward the goal.")] },
      { date: "2026-08-05", pairs: [raw("same", "T", "Alpha has made further progress toward the goal.")] },
      { date: "2026-06-05", pairs: [raw("same", "T", "Alpha has made further progress toward the goal.")] },
    ]);
    expect(sentenceCell(cells[2]).how).toBe("same");
    expect(sentenceCell(cells[1]).how).toBe("same");
    expect(cells[0]).toEqual({ kind: "sentence", parts: [E("Alpha has made"), D(" further"), E(" progress toward the goal.")], how: "changed", para: 1 });
  });

  it("calls a cell New sentence, every word underlined, only right after a gap, whatever the gap's reason", () => {
    for (const gap of ["no_diff", "no_document", "unreadable"] as const) {
      const { cells } = gridOf(ROW, [
        { date: "2026-10-07", pairs: [raw("same", "T", "Alpha holds.")] },
        { date: "2026-08-05", pairs: gap },
        { date: "2026-06-05", pairs: [raw("same", "T", "Alpha holds.")] },
      ]);
      expect(cells[0], gap).toEqual({ kind: "sentence", parts: [I("Alpha holds.")], how: "new", para: 1 });
      expect(cells[1], gap).toMatchObject({ kind: "gap" });
      expect(sentenceCell(cells[2]).how, gap).toBe("same");
    }
    // A row-rule gap (no single matching sentence in the meeting before) is a gap too.
    const { cells } = gridOf(ROW, [
      { date: "2026-10-07", pairs: [raw("same", "T", "Alpha holds.")] },
      { date: "2026-08-05", pairs: [raw("same", "T", "Nothing fits here.")] },
      { date: "2026-06-05", pairs: [raw("same", "T", "Alpha holds.")] },
    ]);
    expect(sentenceCell(cells[0]).how).toBe("new");
    expect(cells[1]).toEqual({ kind: "gap", reason: "none", matched: 0 });
  });

  it("marks the oldest column against the base side of its own diff", () => {
    const { cells } = gridOf(ROW, [{ date: "2019-02-07", pairs: [rawChanged("T", [E("Alpha is "), D("old"), I("new"), E(" today.")], 3)] }]);
    expect(cells[0]).toEqual({ kind: "sentence", parts: [E("Alpha is"), D(" old"), I(" new"), E(" today.")], how: "changed", para: 3 });
  });

  it("leaves the oldest column New sentence when its base side has no single matching sentence", () => {
    const none = gridOf(ROW, [{ date: "2019-02-07", pairs: [raw("added", "T", "Alpha arrives.")] }]).cells[0];
    expect(none).toEqual({ kind: "sentence", parts: [I("Alpha arrives.")], how: "new", para: 1 });
    // two sentences match on the base side (one kept, one removed) but one on the new side: no single base sentence to compare with
    const several = gridOf(ROW, [{ date: "2019-02-07", pairs: [raw("same", "T", "Alpha one."), raw("removed", "T", "Alpha two.", null)] }]).cells[0];
    expect(several).toEqual({ kind: "sentence", parts: [I("Alpha one.")], how: "new", para: 1 });
  });

  it("never carries a column's struck words into the next column's comparison", () => {
    const { cells } = gridOf(ROW, [
      { date: "2026-10-07", pairs: [raw("same", "T", "Alpha one two three four.")] },
      { date: "2026-08-05", pairs: [raw("same", "T", "Alpha one two three.")] },
      { date: "2026-06-05", pairs: [raw("same", "T", "Alpha one two seven.")] },
    ]);
    // Aug struck "seven" and added "three"; Oct is compared with Aug's sentence, which holds no "seven".
    expect(sentenceCell(cells[1]).parts.some((p) => p.op === "del" && p.text.includes("seven"))).toBe(true);
    expect(cells[0]).toEqual({ kind: "sentence", parts: [E("Alpha one two three"), I(" four"), E(".")], how: "changed", para: 1 });
    expect(JSON.stringify(sentenceCell(cells[0]).parts)).not.toContain("seven");
  });

  it("leaves the gaps as they were, with their reasons", () => {
    const { cells } = gridOf(ROW, [
      { date: "2026-10-07", pairs: [raw("same", "T", "Nothing fits.")] },
      { date: "2026-08-05", pairs: "no_diff" },
      { date: "2026-06-05", pairs: "no_document" },
    ]);
    expect(cells).toEqual([
      { kind: "gap", reason: "none", matched: 0 },
      { kind: "gap", reason: "no_diff", matched: 0 },
      { kind: "gap", reason: "no_document", matched: 0 },
    ]);
  });

  it("keeps eq + ins as every cell's sentence and eq + del as the previous column's, over a whole row", () => {
    const sentences = [
      "Alpha holds the stance.",
      "Alpha holds the stance, further.",
      "Alpha holds the stance",
      "Alpha moves the stance to 2-1/2 percent.",
      "Alpha moves the stance to 2‑1/2 percent.",
      "Alpha moves the stance.",
    ];
    const { cells } = gridOf(ROW, sentences.map((text, i) => ({ date: `2026-0${6 - i}-05`, pairs: [raw("same", "T", text)] })));
    for (const [i, cell] of cells.entries()) {
      const c = sentenceCell(cell);
      expect(rebuildNow(c.parts)).toBe(sentences[i]);
      if (i < cells.length - 1) expect(squash(rebuildBefore(c.parts))).toBe(squash(sentences[i + 1]));
    }
  });
});

// The seven cells #510's judge found marked wrongly on its first draw (#530), each from the two consecutive sentences in the
// saved documents (quoted only as far as the case needs). Every one used to read "New sentence" or "Unchanged" wrongly.
describe("buildBankTimeline: the seven cells of the first draw", () => {
  const rbiInflation = TIMELINE_ROWS.RBI.find((r) => r.key === "inflation")!;
  const fedInflation = TIMELINE_ROWS.Fed.find((r) => r.key === "inflation")!;
  const fedDecision = TIMELINE_ROWS.Fed.find((r) => r.key === "decision")!;

  /** Two consecutive meetings' sentences: the newer cell's marks against the older's. */
  function pair(row: RowDef, bank: "RBI" | "Fed", previous: string, current: string, topic = "Inflation") {
    const { cells } = gridOf(row, [{ date: "2024-11-07", pairs: [raw("added", topic, current, 7)] }, { date: "2024-09-18", pairs: [raw("same", topic, previous, 7)] }], bank);
    return sentenceCell(cells[0]);
  }
  const marks = (c: ReturnType<typeof sentenceCell>, op: "ins" | "del") => c.parts.filter((p) => p.op === op).map((p) => p.text);

  it("RBI inflation, 8 Dec 2021 (against 8 Oct): reworded, so Changed with the shared words plain", () => {
    const c = pair(
      rbiInflation,
      "RBI",
      "7. Headline CPI inflation at 5.3 per cent in August softened for the second consecutive month, declining by one percentage point from the recent peak in May-June 2021.",
      "7. Headline CPI inflation, which has been on a downward trajectory since June 2021, edged up to 4.5 per cent in October from 4.3 per cent in September on account of a spike in vegetable prices – due to crop damage from heavy rainfalls in October in several states, and fuel inflation – driven up by international prices of liquefied petroleum gas and kerosene.",
    );
    expect(c.how).toBe("changed");
    expect(c.parts[0]).toEqual(E("7. Headline CPI inflation"));
    expect(marks(c, "del")).toEqual([" at 5.3", " August softened for the second consecutive month, declining by one percentage point", " the recent peak", " May-June 2021"]);
  });

  it("RBI inflation, 10 Feb 2022 (against 8 Dec 2021)", () => {
    const c = pair(
      rbiInflation,
      "RBI",
      "7. Headline CPI inflation, which has been on a downward trajectory since June 2021, edged up to 4.5 per cent in October from 4.3 per cent in September on account of a spike in vegetable prices – due to crop damage from heavy rainfalls in October in several states, and fuel inflation – driven up by international prices of liquefied petroleum gas and kerosene.",
      "7. Headline CPI inflation edged up to 5.6 per cent y-o-y in December from 4.9 per cent in November due to large adverse base effects.",
    );
    expect(c.how).toBe("changed");
    expect(c.parts).toEqual([
      E("7. Headline CPI inflation"),
      D(", which has been on a downward trajectory since June 2021,"),
      E(" edged up to"),
      D(" 4.5"),
      I(" 5.6"),
      E(" per cent"),
      I(" y-o-y"),
      E(" in"),
      D(" October"),
      I(" December"),
      E(" from"),
      D(" 4.3"),
      I(" 4.9"),
      E(" per cent in"),
      D(" September on account of a spike in vegetable prices –"),
      I(" November"),
      E(" due to"),
      D(" crop damage from heavy rainfalls in October in several states, and fuel inflation – driven up by international prices of liquefied petroleum gas and kerosene"),
      I(" large adverse base effects"),
      E("."),
    ]);
  });

  it("RBI inflation, 5 Apr 2024 (against 8 Feb)", () => {
    const c = pair(
      rbiInflation,
      "RBI",
      "5. From its October 2023 trough of 4.9 per cent, CPI inflation increased successively in the next two months to 5.7 per cent by December.",
      "5. Headline inflation softened to 5.1 per cent during January-February 2024, from 5.7 per cent in December.",
    );
    expect(c.parts).toEqual([
      E("5."),
      D(" From its October 2023 trough of 4.9"),
      I(" Headline inflation softened to 5.1"),
      E(" per cent"),
      I(" during January-February 2024"),
      E(","),
      D(" CPI inflation increased successively in the next two months to"),
      I(" from"),
      E(" 5.7 per cent"),
      D(" by"),
      I(" in"),
      E(" December."),
    ]);
  });

  it("RBI inflation, 6 Jun 2025 (against 9 Apr): the paragraph number is a word like any other", () => {
    const c = pair(
      rbiInflation,
      "RBI",
      "5. CPI headline inflation declined by a cumulative 1.6 percentage points during January-February 2025, from 5.2 per cent in December 2024 to a low of 3.6 per cent in February 2025.",
      "6. CPI headline inflation continued its declining trajectory in March and April, with headline CPI inflation moderating to a nearly six-year low of 3.2 per cent (year-on-year) in April 2025.",
    );
    expect(c.how).toBe("changed");
    expect(c.parts.slice(0, 3)).toEqual([D("5"), I("6"), E(". CPI headline inflation")]);
    expect(marks(c, "ins")).toEqual(["6", " continued its declining trajectory", " March and April, with headline CPI inflation moderating", " nearly six-year", " 3.2", " (year-on-year)", " April"]);
    expect(marks(c, "del")).toEqual(["5", " declined by a cumulative 1.6 percentage points during January-February 2025, from 5.2 per cent", " December 2024", " 3.6", " February"]);
  });

  it("Fed inflation, 28 Jan 2026 (against 10 Dec 2025): '…remains somewhat elevated' carries over, so it isn't New sentence", () => {
    const c = pair(fedInflation, "Fed", "Inflation has moved up since earlier in the year and remains somewhat elevated.", "Inflation remains somewhat elevated.");
    expect(c.how).toBe("changed");
    expect(c.parts).toEqual([E("Inflation"), D(" has moved up since earlier in the year and"), E(" remains somewhat elevated.")]);
  });

  it("Fed inflation, 7 Nov 2024 (against 18 Sep): 'further' was removed, so Changed and not Unchanged", () => {
    const c = pair(
      fedInflation,
      "Fed",
      "Inflation has made further progress toward the Committee's 2 percent objective but remains somewhat elevated.",
      "Inflation has made progress toward the Committee's 2 percent objective but remains somewhat elevated.",
    );
    expect(c.how).toBe("changed");
    expect(c.parts).toEqual([E("Inflation has made"), D(" further"), E(" progress toward the Committee's 2 percent objective but remains somewhat elevated.")]);
  });

  it("Fed decision, 30 Jan 2019 (the oldest column, against Dec 2018): '2-1/2' is not marked for a non-breaking hyphen", () => {
    const base = "In view of realized and expected labor market conditions and inflation, the Committee decided to raise the target range for the federal funds rate to 2-1/4 to 2‑1/2 percent.";
    const now = "In support of these goals, the Committee decided to maintain the target range for the federal funds rate at 2-1/4 to 2-1/2 percent.";
    const { cells } = gridOf(fedDecision, [{ date: "2019-01-30", pairs: [rawChanged("Decision and guidance", [D(base), I(now)], 3)] }], "Fed");
    const c = sentenceCell(cells[0]);
    expect(c.how).toBe("changed");
    expect(c.parts).toEqual([
      E("In"),
      D(" view"),
      I(" support"),
      E(" of"),
      D(" realized and expected labor market conditions and inflation"),
      I(" these goals"),
      E(", the Committee decided to"),
      D(" raise"),
      I(" maintain"),
      E(" the target range for the federal funds rate"),
      D(" to"),
      I(" at"),
      E(" 2-1/4 to 2-1/2 percent."),
    ]);
    expect(marks(c, "ins").join("")).not.toContain("2-1/2");
  });
});

// ------------------------------------------------------------------------------------------------ the grid's shape

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

  it("marks a sentence cell against the previous column: Aug's stance is the same words as Oct's", () => {
    expect(grid.columns[0].cells.stance).toMatchObject({ kind: "sentence", how: "same" }); // vs Aug's sentence
    expect(grid.columns[1].cells.stance).toMatchObject({ kind: "sentence", how: "new" }); // Jun is an unreadable diff: a gap before it
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
