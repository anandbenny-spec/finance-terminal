// The pieces /policy and the front page (#152, the second use) share: a panel's footer (SourceLine and Freshness),
// the error panel for a failed read, and the latest of several dates. Moved out of app/policy/page.tsx unchanged.
import type { ReactNode } from "react";
import { Freshness } from "@/components/data/Freshness";
import { Panel, type PanelSpan } from "@/components/data/Panel";
import { SourceLine } from "@/components/data/SourceLine";
import { StateNote } from "@/components/data/StateNote";
import type { TaskKey } from "@/lib/feeds";
import type { TaskFreshnessRow } from "@/lib/queries/api";
import { panelFreshness } from "@/lib/shell/freshness";

export function latestOf(...dates: (string | null | undefined)[]): string | null {
  return dates.filter((d): d is string => Boolean(d)).sort().at(-1) ?? null;
}

export function Foot({
  sources,
  tasks,
  dataTo,
  freshness,
  now,
}: {
  sources: (string | null | undefined)[];
  tasks: readonly TaskKey[];
  dataTo: string | null;
  freshness: TaskFreshnessRow[] | null;
  now: string;
}) {
  return (
    <>
      <SourceLine lines={sources} />
      <Freshness freshness={panelFreshness(freshness, tasks, dataTo, now)} />
    </>
  );
}

export function ErrorPanel({
  title,
  id,
  span,
  market,
  footer,
  refresh,
}: {
  title: string;
  id: string;
  span?: PanelSpan;
  market?: "US" | "IN";
  footer?: ReactNode;
  refresh?: readonly TaskKey[];
}) {
  return (
    <Panel title={title} titleId={id} span={span} market={market} footer={footer} refresh={refresh}>
      <StateNote kind="error">Couldn&apos;t load this panel. Try again.</StateNote>
    </Panel>
  );
}
