"use client";

// /admin/labelling (Prompt 01 E1; Architecture 03 §1.9): Anand labels Pass 2's evaluation sets blind, one item at a
// time, the decision paragraph highlighted with the full text below, a label and a one-line reason. Admin only:
// anyone else is asked to log in (the texts are published documents, so nothing here is private; the gate keeps the
// page to its one labeller). No model output anywhere: it reads documents, chunks and rate settings only, never
// ai_outputs or a stance or score column. RBI's drafted stance appears only after Anand saves his own label.
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { DocumentView } from "@/components/ai/DocumentView";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { useAdmin } from "@/components/shell/admin";
import rawItems from "@/lib/labelling/items.json";
import {
  REASON_MAX,
  SET_NAMES,
  SET_TITLE,
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
  type ItemText,
  type Labels,
  type SetName,
} from "@/lib/labelling/labelling";
import { formatCount, formatDate } from "@/lib/format";
import { documentSource } from "@/lib/policy/document";
import { latestByType } from "@/lib/policy/meeting";
import { textFromChunks } from "@/lib/policy/text";
import { readChunks, readMeetingDocuments, readMeetingSettings } from "@/lib/queries/policy";

const ITEMS = parseItems(rawItems);
const GUIDE = "https://github.com/anandbenny-spec/finance-terminal/blob/main/py/config/eval/sets/GUIDE.md";
const DOC_LABEL = { fomc_statement: "FOMC statement", mpc_resolution: "MPC resolution", mpc_minutes: "MPC minutes" } as const;
const INPUT =
  "h-9 w-full rounded-md border border-line-input bg-raised px-2.5 text-ui text-ink-1 placeholder:text-ink-3 focus-visible:outline-2 focus-visible:outline-amber";

type Loaded = { state: "loading" } | { state: "error" } | { state: "missing" } | { state: "ok"; text: ItemText; source: string };

async function loadItem(item: Item): Promise<Loaded> {
  const { browserClient } = await import("@/lib/supabase/browser");
  const db = browserClient();
  if (!db) return { state: "error" };
  try {
    const doc = latestByType(await readMeetingDocuments(db, item.meeting)).get(item.doc_type);
    if (!doc) return { state: "missing" };
    const chunks = await readChunks(db, doc.id);
    const full = textFromChunks(chunks);
    const source = documentSource(doc, item.bank);
    if (item.doc_type === "mpc_minutes") {
      const text = item.speaker ? memberText(full, chunks, item.speaker) : null;
      return text ? { state: "ok", text, source } : { state: "missing" };
    }
    return { state: "ok", text: decisionText(item.bank, doc.id, full, await readMeetingSettings(db, item.meeting)), source };
  } catch {
    return { state: "error" };
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

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

function ItemForm({ set, item, saved, onSave }: { set: SetName; item: Item; saved: Labels[string] | undefined; onSave: (label: string, reason: string) => void }) {
  const [label, setLabel] = useState(saved?.label ?? "");
  const [reason, setReason] = useState(saved?.reason ?? "");
  const ok = validLabel(set, item, label, reason);
  const draft = set === "policy_stance" && item.bank === "RBI" ? ITEMS?.rbi_stance_drafts[item.id] : undefined;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (ok) onSave(label, reason.trim());
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1 text-meta text-ink-2">{set === "policy_stance" ? (item.bank === "Fed" ? "Guidance direction" : "Stated stance") : "Score"}</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          {choices(set, item).map((c) => (
            <label key={c} className="inline-flex items-center gap-1.5 text-ui text-ink-1">
              <input type="radio" name="label" value={c} checked={label === c} onChange={() => setLabel(c)} className="accent-amber" />
              <span className={set === "policy_stance" ? "" : "font-mono"}>{c.replace("-", "−")}</span>
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
      {saved && draft && (
        <p className="text-meta text-ink-2">
          Claude&apos;s draft: <span className="text-ink-1">{draft.label}</span>
          {draft.label === saved.label ? " (agrees with yours)" : " (differs from yours: it goes to the disagreements)"}
        </p>
      )}
    </form>
  );
}

function Labeller({ set }: { set: SetName }) {
  const items = useMemo(() => ITEMS?.sets[set] ?? [], [set]);
  const key = storageKey(set, ITEMS?.rule_version ?? 0);
  // The page renders only in the browser, after the admin check, so the stored labels can be read at once.
  const [labels, setLabels] = useState<Labels>(() => loadLabels(storage(), key));
  const [stored, setStored] = useState(true);
  const [at, setAt] = useState(() => nextUnlabelled(items, labels));
  const [result, setResult] = useState<{ id: string; loaded: Loaded } | null>(null);

  const item = items[at];
  const loaded: Loaded = item && result?.id === item.id ? result.loaded : { state: "loading" };
  useEffect(() => {
    if (!item) return;
    let alive = true;
    loadItem(item).then((r) => {
      if (alive) setResult({ id: item.id, loaded: r });
    });
    return () => {
      alive = false;
    };
  }, [item]);

  const save = useCallback(
    (label: string, reason: string) => {
      if (!item) return;
      const next = { ...labels, [item.id]: { label, reason, saved_at: new Date().toISOString() } };
      setLabels(next);
      setStored(storeLabels(storage(), key, next));
    },
    [item, labels, key],
  );

  if (items.length === 0)
    return <StateNote kind="empty">This set&apos;s items haven&apos;t been chosen yet (py/config/eval/sets/labelling_items.json).</StateNote>;
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
        <button
          type="button"
          onClick={() => ITEMS && download(`${set}.labels.json`, exportPayload(ITEMS, set, labels, new Date().toISOString()))}
          className="h-8 rounded-md border border-line-input px-3 text-ui text-ink-1 hover:bg-active"
        >
          Export labels
        </button>
        {!stored && <StateNote kind="error">This browser didn&apos;t keep the last label: export now.</StateNote>}
      </div>
      <h2 className="text-data font-medium text-ink-1">
        Item {formatCount(at + 1)} of {formatCount(items.length)} · {item.bank} · {DOC_LABEL[item.doc_type]}
        {item.speaker ? ` · ${item.speaker}` : ""} · {formatDate(item.decision_date)}
      </h2>
      <ItemForm key={item.id} set={set} item={item} saved={labels[item.id]} onSave={save} />
      {loaded.state === "loading" && <StateNote kind="empty">Reading the text…</StateNote>}
      {loaded.state === "error" && <StateNote kind="error">Couldn&apos;t read this document. Try again.</StateNote>}
      {loaded.state === "missing" && <StateNote kind="unavailable">This item&apos;s text isn&apos;t published on this project.</StateNote>}
      {loaded.state === "ok" && (
        <>
          <div className="text-meta text-ink-2">
            <SourceLine lines={[loaded.source]} />
          </div>
          {loaded.text.highlight ? (
            <section aria-label="Decision paragraph" className="rounded-md border border-line-strong bg-raised px-3 py-2">
              <p className="mb-1 text-meta text-ink-2">{item.speaker ? "Where the member states their vote" : "The decision paragraph"} (¶{loaded.text.highlight.n})</p>
              <p className="text-read text-ink-1">{loaded.text.highlight.text}</p>
            </section>
          ) : (
            <StateNote kind="unavailable">No decision paragraph was found: read the full text.</StateNote>
          )}
          <DocumentView
            paragraphs={loaded.text.paras}
            mark={loaded.text.highlight ? [loaded.text.highlight.start, loaded.text.highlight.end] : null}
            label={`${DOC_LABEL[item.doc_type]}, full text`}
          />
        </>
      )}
    </div>
  );
}

export function LabellingView() {
  const { isAdmin, ready } = useAdmin();
  const [set, setSet] = useState<SetName>(SET_NAMES[0]);

  if (!ready) return <StateNote kind="empty">Checking your session…</StateNote>;
  if (!isAdmin)
    return (
      <StateNote kind="unavailable">
        For the admin only.{" "}
        <Link href="/admin" className="text-ink-1 underline">
          Log in
        </Link>
      </StateNote>
    );
  if (!ITEMS) return <StateNote kind="error">The items file couldn&apos;t be read.</StateNote>;

  return (
    <PanelGrid>
      <Panel
        title="Label one item at a time"
        titleId="panel-labelling"
        subtitle={
          <>
            Score from the text alone, using the{" "}
            <a href={GUIDE} className="text-ink-1 underline">
              scoring guide
            </a>
            . Labels stay in this browser until you export them.
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <div role="group" aria-label="Set" className="flex flex-wrap gap-2">
            {SET_NAMES.map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={s === set}
                onClick={() => setSet(s)}
                className="h-8 rounded-md border border-line-input px-3 text-ui text-ink-1 hover:bg-active aria-pressed:bg-active"
              >
                {SET_TITLE[s]} ({formatCount(ITEMS.sets[s].length)})
              </button>
            ))}
          </div>
          <Labeller key={set} set={set} />
        </div>
      </Panel>
    </PanelGrid>
  );
}
