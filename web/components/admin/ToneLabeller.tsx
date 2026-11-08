"use client";

// The tone gate's labelling (Spec 02B §5.8; job 2B-12, #175): one management passage at a time, a score from -2 to +2 and a
// one-line reason, blind. The passages' text is private, so it comes from `GET /api/admin/labelling/items` (the admin token is
// checked there); nothing here reads a transcript, a company, a result or a model output. Labels stay in this browser until
// exported.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { StateNote } from "@/components/data/StateNote";
import { adminFetch } from "@/lib/admin/api";
import { formatCount } from "@/lib/format";
import { REASON_MAX } from "@/lib/labelling/labelling";
import {
  SECTION_LABEL,
  TONE_SCORES,
  loadLabels,
  nextUnlabelledTone,
  parseToneItems,
  storeLabels,
  toneExport,
  toneStorageKey,
  validToneLabel,
  type Labels,
  type ToneItem,
} from "@/lib/labelling/tone";

const INPUT =
  "h-9 w-full rounded-md border border-line-input bg-raised px-2.5 text-ui text-ink-1 placeholder:text-ink-3 focus-visible:outline-2 focus-visible:outline-highlight";

type Loaded = { state: "loading" } | { state: "error"; status: number } | { state: "ok"; items: ToneItem[] };

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

function download(name: string, body: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 2) + "\n"], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function Form({ saved, onSave }: { saved: Labels[string] | undefined; onSave: (label: string, reason: string) => void }) {
  const [label, setLabel] = useState(saved?.label ?? "");
  const [reason, setReason] = useState(saved?.reason ?? "");
  const ok = validToneLabel(label, reason);
  function submit(e: FormEvent) {
    e.preventDefault();
    if (ok) onSave(label, reason.trim());
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1 text-meta text-ink-2">Score</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          {TONE_SCORES.map((c) => (
            <label key={c} className="inline-flex items-center gap-1.5 text-ui text-ink-1">
              <input type="radio" name="label" value={c} checked={label === c} onChange={() => setLabel(c)} className="accent-highlight" />
              <span className="font-mono">{c.replace("-", "−")}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="flex flex-col gap-1 text-meta text-ink-2">
        Reason (one line)
        <input value={reason} maxLength={REASON_MAX} onChange={(e) => setReason(e.target.value)} className={INPUT} />
      </label>
      <div className="flex items-center gap-3">
        <button type="submit" disabled={!ok} className="h-9 rounded-md bg-primary px-3 text-ui font-medium text-primary-foreground hover:bg-primary/80 disabled:opacity-50">
          {saved ? "Update label" : "Save label"}
        </button>
        {saved && <span className="text-meta text-ink-2">Saved: {saved.label.replace("-", "−")}</span>}
      </div>
    </form>
  );
}

function Labeller({ items }: { items: ToneItem[] }) {
  const key = toneStorageKey();
  const [labels, setLabels] = useState<Labels>(() => loadLabels(storage(), key));
  const [stored, setStored] = useState(true);
  const [at, setAt] = useState(() => nextUnlabelledTone(items, labels));
  const item = items[at];
  const save = useCallback(
    (label: string, reason: string) => {
      if (!item) return;
      const next = { ...labels, [item.id]: { label, reason, saved_at: new Date().toISOString() } };
      setLabels(next);
      setStored(storeLabels(storage(), key, next));
    },
    [item, labels, key],
  );
  const done = items.filter((it) => labels[it.id]).length;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3 text-meta text-ink-2">
        <span>
          {formatCount(done)} of {formatCount(items.length)} labelled
        </span>
        <button type="button" onClick={() => setAt(Math.max(at - 1, 0))} disabled={at === 0} className="h-8 rounded-md border border-line-input px-3 text-ui text-ink-1 hover:bg-active disabled:opacity-50">
          Previous
        </button>
        <button type="button" onClick={() => setAt(Math.min(at + 1, items.length - 1))} disabled={at === items.length - 1} className="h-8 rounded-md border border-line-input px-3 text-ui text-ink-1 hover:bg-active disabled:opacity-50">
          Next
        </button>
        <button type="button" onClick={() => download("earnings_tone.labels.json", toneExport(items, labels, new Date().toISOString()))} className="h-8 rounded-md border border-line-input px-3 text-ui text-ink-1 hover:bg-active">
          Export labels
        </button>
        {!stored && <StateNote kind="error">This browser didn&apos;t keep the last label: export now.</StateNote>}
      </div>
      <h2 className="text-data font-medium text-ink-1">
        Passage {formatCount(at + 1)} of {formatCount(items.length)} · {SECTION_LABEL[item.section]}
      </h2>
      <Form key={item.id} saved={labels[item.id]} onSave={save} />
      <section aria-label="The passage" className="rounded-md border border-line-strong bg-raised px-3 py-2">
        <p className="whitespace-pre-wrap text-read text-ink-1">{item.text}</p>
      </section>
    </div>
  );
}

export function ToneLabeller() {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  useEffect(() => {
    let alive = true;
    adminFetch<unknown>("/api/admin/labelling/items?set=earnings_tone").then((r) => {
      if (!alive) return;
      const parsed = r.ok ? parseToneItems(r.body) : null;
      setLoaded(parsed ? { state: "ok", items: parsed.items } : { state: "error", status: r.ok ? 502 : r.status });
    });
    return () => {
      alive = false;
    };
  }, []);
  if (loaded.state === "loading") return <StateNote kind="empty">Reading the passages…</StateNote>;
  if (loaded.state === "error")
    return <StateNote kind="error">Couldn&apos;t read the passages (status {loaded.status}). They come from the admin route, which needs its database function; try again later.</StateNote>;
  if (loaded.items.length === 0)
    return <StateNote kind="empty">The 30 passages haven&apos;t been chosen yet (py/config/eval/sets/earnings_tone_items.json).</StateNote>;
  return <Labeller items={loaded.items} />;
}
