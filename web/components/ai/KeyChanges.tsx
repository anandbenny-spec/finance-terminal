"use client";

// KeyChanges (Design 01 §1.16 layer 2, §4.5): the default view of "What changed". A switch, Key changes / Full redline;
// Key changes is a table by topic: Before (the old sentence, removed words struck through) · Now (the new sentence,
// added words underlined), each side with a ¶ link into its document; a wholly added or removed sentence reads "added"
// or "removed" in italics on the other side. It stacks as "Before / Now" below `md`. The closing line counts the
// unchanged paragraphs. Every word is one the stored diff holds; topics come from the documents' own structure.
import Link from "next/link";
import { useState, type ReactNode } from "react";
import { formatAxisNumber } from "@/lib/format";
import { previewKeyChanges, type KeyChanges as Model, type KeySide } from "@/lib/policy/keychanges";

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
        <p className="break-words text-ui text-ink-1">
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
        <p className="text-ui italic text-ink-3">{absent}</p>
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
  limit,
  seeAllHref,
}: {
  model: Model;
  before: KeyDoc;
  now: KeyDoc;
  /** Not needed in the preview (`limit`). */
  unchangedParagraphs?: number;
  /** The full redline, shown behind the switch. Not needed in the preview. */
  redline?: ReactNode;
  /**
   * The preview (the front page, #152): only the first `limit` rows in Key changes' own order, no Key changes / Full
   * redline switch, ending with how many other sentences changed and a link to all of them (`seeAllHref`).
   */
  limit?: number;
  seeAllHref?: string;
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
  const preview = limit !== undefined ? previewKeyChanges(model, limit) : null;
  const groups = preview ? preview.groups : model.groups;
  return (
    <div>
      {!preview && <div className="mb-3 inline-flex gap-1 rounded-md border border-line p-0.5" role="group" aria-label="Show the changes as">
        {tab("key", "Key changes")}
        {tab("full", "Full redline")}
      </div>}
      {!preview && view === "full" ? (
        redline
      ) : groups.length === 0 ? (
        <p className="text-data text-ink-2">No sentence changed against the previous one.</p>
      ) : (
        <>
          <div className="hidden gap-x-4 border-b border-line pb-1 text-meta text-ink-3 md:grid md:grid-cols-5">
            <span>Topic</span>
            <span className="md:col-span-2">Before ({before.date})</span>
            <span className="md:col-span-2">Now ({now.date})</span>
          </div>
          {groups.map((g) => (
            <section key={g.topic} aria-label={g.topic} className="border-b border-line py-3">
              {g.rows.map((r, i) => (
                <div key={r.key} className="grid gap-x-4 gap-y-2 py-2.5 md:grid-cols-5">
                  <span className="text-meta font-medium text-ink-2">{i === 0 ? g.topic : <span className="sr-only">{g.topic}</span>}</span>
                  <Side side={r.before} label={`Before (${before.date})`} doc={before} absent="added" />
                  <Side side={r.now} label={`Now (${now.date})`} doc={now} absent="removed" />
                </div>
              ))}
            </section>
          ))}
          {preview ? (
            <p className="mt-3 text-meta text-ink-3">
              {preview.others > 0 && (
                <>
                  {formatAxisNumber(preview.others, 0)} other {preview.others === 1 ? "sentence" : "sentences"} changed ·{" "}
                </>
              )}
              {seeAllHref && (
                <Link href={seeAllHref} className="text-ink-1 underline">
                  See all changes →
                </Link>
              )}
            </p>
          ) : (
            unchangedParagraphs !== undefined && (
              <p className="mt-3 text-meta text-ink-3">
                {formatAxisNumber(model.changedSentences, 0)} {model.changedSentences === 1 ? "sentence" : "sentences"} changed · {formatAxisNumber(unchangedParagraphs, 0)}{" "}
                {unchangedParagraphs === 1 ? "paragraph" : "paragraphs"} unchanged
              </p>
            )
          )}
        </>
      )}
    </div>
  );
}
