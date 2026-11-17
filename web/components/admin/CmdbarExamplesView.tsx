"use client";

// /admin/cmdbar (Spec 07 §4 Examples, §9 step 4; K-09 part 2, #588): the command bar's example chips, each with its stored answer
// as the box shows it, checked or not. "Mark checked" lets a chip's answer reach the box; only an answer can be checked, and a
// mark is tied to the answer on the page (if the daily task rewrote it since, the page reads the list again). Admin only: for
// anyone else the page asks them to log in, and the route refuses them anyway. The answers are the `cmdbar.examples` task's.
import { CircleCheck, CircleMinus, RotateCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AnswerView } from "@/components/cmdbar/AnswerView";
import { Panel, PanelGrid } from "@/components/data/Panel";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import { useAdmin } from "@/components/shell/admin";
import { markExample, readAdminExamples, type AdminExample, type CheckOutcome, type ReadResult } from "@/lib/admin/cmdbar";
import { formatCheckedTime, formatCount } from "@/lib/format";

const SOURCE = "Source: the command bar's example answers (ops), stored by the daily cmdbar.examples task";
const BUTTON =
  "inline-flex h-8 items-center gap-1.5 rounded-md border border-line-input px-3 text-ui text-ink-1 hover:bg-active disabled:opacity-50";

const OUTCOME_TEXT: Record<Exclude<CheckOutcome, "done">, string> = {
  changed: "The answer changed since this page read it. The list has been read again: check the new answer.",
  not_answered: "Only an answer can be marked checked.",
  failed: "Couldn't save the mark. Try again.",
};

function CheckedWord({ verified }: { verified: boolean }) {
  const Icon = verified ? CircleCheck : CircleMinus;
  return (
    <span className="inline-flex items-center gap-1.5 text-meta text-ink-2">
      <Icon aria-hidden className="size-3.5 shrink-0 text-ink-3" />
      {verified ? "Checked: the box shows it" : "Not checked: the box doesn't show it"}
    </span>
  );
}

/** One chip: its question, its answer as the box shows it, and the mark. Exported for the tests. */
export function ExampleCard({
  example,
  now,
  busy,
  note,
  onMark,
}: {
  example: AdminExample;
  now: string;
  busy: boolean;
  note: string | null;
  onMark: (verified: boolean) => void;
}) {
  const titleId = `panel-example-${example.id}`;
  return (
    <Panel
      title={example.question}
      titleId={titleId}
      span={12}
      subtitle={
        <span>
          {example.module ?? "No module"} · chip {example.displayOrder === null ? "–" : formatCount(example.displayOrder)}
          {example.updatedAt ? ` · answer stored ${formatCheckedTime(example.updatedAt, now)}` : ""}
        </span>
      }
      footer={<SourceLine lines={[SOURCE]} />}
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <CheckedWord verified={example.verified} />
          {example.verified ? (
            <button type="button" className={BUTTON} disabled={busy} onClick={() => onMark(false)}>
              Take the mark off
            </button>
          ) : (
            <button type="button" className={BUTTON} disabled={busy || !example.answerable} onClick={() => onMark(true)}>
              Mark checked
            </button>
          )}
          {!example.verified && !example.answerable && (
            <span className="text-meta text-ink-2">Only an answer can be marked checked.</span>
          )}
        </div>
        {note && (
          <p role="status">
            <StateNote kind="error">{note}</StateNote>
          </p>
        )}
        <section aria-label="The stored answer" className="rounded-md border border-line-strong px-3 py-2">
          {example.answer ? (
            <AnswerView answer={example.answer} now={now} onNavigate={() => {}} />
          ) : (
            <StateNote kind="empty">No answer is stored yet: the daily task stores one on its next run.</StateNote>
          )}
        </section>
      </div>
    </Panel>
  );
}

/** The list, given the data (exported for the tests). */
export function ExampleList({
  examples,
  now,
  busy,
  notes,
  onMark,
}: {
  examples: AdminExample[];
  now: string;
  busy: number | null;
  notes: Record<number, string>;
  onMark: (example: AdminExample, verified: boolean) => void;
}) {
  if (examples.length === 0)
    return <StateNote kind="empty">No example answers are stored yet. The daily task stores them once it is enabled.</StateNote>;
  const checked = examples.filter((e) => e.verified).length;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-ink-2">
        {formatCount(checked)} of {formatCount(examples.length)} checked. Only checked answers appear as chips in the box.
      </p>
      <PanelGrid>
        {examples.map((e) => (
          <ExampleCard key={e.id} example={e} now={now} busy={busy !== null} note={notes[e.id] ?? null} onMark={(v) => onMark(e, v)} />
        ))}
      </PanelGrid>
    </div>
  );
}

export function CmdbarExamplesView() {
  const { isAdmin, ready } = useAdmin();
  const [read, setRead] = useState<ReadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  // The time the list was read (for "answer stored …"): set with each read, never during render (Next prerenders this page).
  const [now, setNow] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const r = await readAdminExamples();
    setRead(r);
    setNow(new Date().toISOString());
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!ready || !isAdmin) return;
    let alive = true;
    void readAdminExamples().then((r) => {
      if (!alive) return;
      setRead(r);
      setNow(new Date().toISOString());
    });
    return () => {
      alive = false;
    };
  }, [ready, isAdmin]);

  const mark = useCallback(
    async (example: AdminExample, verified: boolean) => {
      setBusy(example.id);
      const { outcome } = await markExample(example, verified);
      setNotes((n) => {
        const next = { ...n };
        if (outcome === "done") delete next[example.id];
        else next[example.id] = OUTCOME_TEXT[outcome];
        return next;
      });
      await load();
      setBusy(null);
    },
    [load],
  );

  if (!ready) return <StateNote kind="empty">Checking your session…</StateNote>;
  if (!isAdmin)
    return (
      <StateNote kind="unavailable">
        This page is for the admin.{" "}
        <Link prefetch={false} href="/admin" className="text-ink-1 underline">
          Log in
        </Link>
      </StateNote>
    );
  return (
    <div className="flex flex-col gap-3">
      <div>
        <button type="button" onClick={() => void load()} disabled={loading} className={BUTTON}>
          <RotateCw aria-hidden className="size-4" />
          {loading ? "Reading…" : "Read again"}
        </button>
      </div>
      {read?.examples && now ? (
        <ExampleList examples={read.examples} now={now} busy={busy} notes={notes} onMark={(e, v) => void mark(e, v)} />
      ) : loading || read === null ? (
        <StateNote kind="empty">Reading the example answers…</StateNote>
      ) : read.status === 401 || read.status === 403 ? (
        <StateNote kind="error">Your admin session has ended. Log in again.</StateNote>
      ) : (
        <StateNote kind="error">
          Couldn&apos;t read the example answers (status {read.status}). They come from the admin route, which needs migration 0055 on
          this project; try again later.
        </StateNote>
      )}
    </div>
  );
}
