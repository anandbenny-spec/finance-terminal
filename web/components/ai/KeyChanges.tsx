"use client";

// KeyChanges (Design 01 §1.16 layer 2, §4.5): the default view of "What changed". A switch, Key changes / Full redline;
// Key changes is a table by topic: Before (the old sentence, removed words struck through) · Now (the new sentence,
// added words underlined), each side with a ¶ link into its document; a wholly added or removed sentence reads "added"
// or "removed" in italics on the other side. It stacks as "Before / Now" below `md`. The closing line counts the
// unchanged paragraphs. Every word is one the stored diff holds; topics come from the documents' own structure.
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { formatAxisNumber } from "@/lib/format";
import type { KeyChanges as Model, KeySide } from "@/lib/policy/keychanges";

const TAB_ON = "bg-raised text-ink-1";
const TAB_OFF = "text-ink-2 hover:text-ink-1";

export type KeyDoc = { href: string; date: string };

function Words({ side }: { side: NonNullable<KeySide> }) {
  return (
    <>
      {side.ops.map((o, i) => (o.op === "eq" ? <span key={i}>{o.text}</span> : o.op === "ins" ? <ins key={i}>{o.text}</ins> : <del key={i}>{o.text}</del>))}
    </>
  );
}

function Side({ side, label, doc, absent }: { side: KeySide; label: string; doc: KeyDoc; absent: string }) {
  return (
    <div className="min-w-0 md:col-span-2">
      <span className="mb-1 block text-meta text-ink-3 md:hidden">{label}</span>
      {side ? (
        <p className="break-words text-data text-ink-1">
          <Words side={side} />
          {side.para !== null && (
            <>
              {" "}
              <Link href={`${doc.href}#p-${side.para}`} className="whitespace-nowrap font-mono text-meta text-ink-3 underline hover:text-ink-1" aria-label={`${label}: paragraph ${side.para}`}>
                ¶{side.para}
              </Link>
            </>
          )}
        </p>
      ) : (
        <p className="text-data italic text-ink-3">{absent}</p>
      )}
    </div>
  );
}

export function KeyChanges({
  model,
  before,
  now,
  unchangedParagraphs,
  redline,
}: {
  model: Model;
  before: KeyDoc;
  now: KeyDoc;
  unchangedParagraphs: number;
  /** The full redline, shown behind the switch. */
  redline: ReactNode;
}) {
  const [view, setView] = useState<"key" | "full">("key");
  const tab = (id: "key" | "full", text: string) => (
    <button
      type="button"
      onClick={() => setView(id)}
      aria-pressed={view === id}
      className={`rounded-sm px-3 py-1.5 text-meta ${view === id ? TAB_ON : TAB_OFF}`}
    >
      {text}
    </button>
  );
  return (
    <div>
      <div className="mb-3 inline-flex gap-1 rounded-md border border-line p-0.5" role="group" aria-label="Show the changes as">
        {tab("key", "Key changes")}
        {tab("full", "Full redline")}
      </div>
      {view === "full" ? (
        redline
      ) : model.groups.length === 0 ? (
        <p className="text-data text-ink-2">No sentence changed against the previous one.</p>
      ) : (
        <>
          <div className="hidden gap-x-4 border-b border-line pb-1 text-meta text-ink-3 md:grid md:grid-cols-5">
            <span>Topic</span>
            <span className="md:col-span-2">Before ({before.date})</span>
            <span className="md:col-span-2">Now ({now.date})</span>
          </div>
          {model.groups.map((g) => (
            <section key={g.topic} aria-label={g.topic} className="border-b border-line py-3">
              {g.rows.map((r, i) => (
                <div key={r.key} className="grid gap-x-4 gap-y-2 py-2 md:grid-cols-5">
                  <span className="text-meta font-medium text-ink-2">{i === 0 ? g.topic : <span className="sr-only">{g.topic}</span>}</span>
                  <Side side={r.before} label={`Before (${before.date})`} doc={before} absent="added" />
                  <Side side={r.now} label={`Now (${now.date})`} doc={now} absent="removed" />
                </div>
              ))}
            </section>
          ))}
          <p className="mt-3 text-meta text-ink-3">
            {formatAxisNumber(model.changedSentences, 0)} {model.changedSentences === 1 ? "sentence" : "sentences"} changed · {formatAxisNumber(unchangedParagraphs, 0)}{" "}
            {unchangedParagraphs === 1 ? "paragraph" : "paragraphs"} unchanged
          </p>
        </>
      )}
    </div>
  );
}
