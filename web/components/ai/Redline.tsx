"use client";

// Redline (Design 01 §1.16 layer 3, §4.5): the statement against the previous one, matched sentence by sentence,
// as a unified inline diff: <ins> underlined on the green wash, <del> struck through on the red wash (globals.css
// styles both), so screen readers announce them and colour is never the only cue. A legend gives the word counts.
// "Changes only" (on by default) folds paragraphs where nothing changed into "3 unchanged paragraphs", which expand.
// Works at phone width: nothing sits side by side. Every word shown is one the stored diff holds.
import { useState } from "react";
import { formatAxisChange, formatAxisNumber } from "@/lib/format";
import type { Redline as Model, RedlinePair } from "@/lib/policy/redline";

function Pair({ pair }: { pair: RedlinePair }) {
  if (pair.kind !== "changed") {
    if (pair.kind === "added") return <><ins>{pair.text}</ins> </>;
    if (pair.kind === "removed") return <><del>{pair.text}</del> </>;
    return <>{pair.text} </>;
  }
  return (
    <>
      {pair.ops.map((o, i) => (o.op === "eq" ? <span key={i}>{o.text}</span> : o.op === "ins" ? <ins key={i}>{o.text}</ins> : <del key={i}>{o.text}</del>))}{" "}
    </>
  );
}

export function Redline({ model, added, removed }: { model: Model; added: number; removed: number }) {
  const [changesOnly, setChangesOnly] = useState(true);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const blocks: ({ kind: "para"; key: string; para: Model["paragraphs"][number] } | { kind: "fold"; key: string; keys: string[] })[] = [];
  for (const p of model.paragraphs) {
    if (!changesOnly || p.changed || open.has(p.key)) blocks.push({ kind: "para", key: p.key, para: p });
    else {
      const last = blocks.at(-1);
      if (last?.kind === "fold") last.keys.push(p.key);
      else blocks.push({ kind: "fold", key: `fold-${p.key}`, keys: [p.key] });
    }
  }
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-meta text-ink-2">
        <span>
          <ins>Added</ins> / <del>Removed</del>
        </span>
        <span className="font-mono">
          {formatAxisChange(added)} / {formatAxisChange(-removed)} words
        </span>
        <label className="ml-auto inline-flex cursor-pointer items-center gap-1.5">
          <input type="checkbox" checked={changesOnly} onChange={(e) => setChangesOnly(e.target.checked)} className="size-4 accent-amber" />
          Changes only
        </label>
      </div>
      <div className="max-w-(--reading-width) text-read text-ink-1">
        {blocks.map((b) =>
          b.kind === "para" ? (
            <p key={b.key} className="mb-4">
              {b.para.pairs.map((pair, i) => (
                <Pair key={i} pair={pair} />
              ))}
              {b.para.para !== null && <span className="font-mono text-meta text-ink-3">¶{b.para.para}</span>}
            </p>
          ) : (
            <p key={b.key} className="mb-4">
              <button
                type="button"
                onClick={() => setOpen(new Set([...open, ...b.keys]))}
                className="text-meta text-ink-3 underline hover:text-ink-1"
              >
                {b.keys.length === 1 ? "1 unchanged paragraph" : `${formatAxisNumber(b.keys.length, 0)} unchanged paragraphs`}
              </button>
            </p>
          ),
        )}
      </div>
    </div>
  );
}
