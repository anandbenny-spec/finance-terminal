// StateNote (Design 01 §4.2): an icon and one sentence where a panel or shell item has nothing to show, so nothing
// is ever blank. Kinds: empty, held (held back until evaluation), delayed (clock, orange: the last good data
// stays), unavailable (not published by the source yet), error (couldn't load). Visitors never see the reason
// behind an error (Arch 02); the copy for each case is Spec 01 §4.5's.
import { cn } from "cn";
import { CircleAlert, Clock, Info } from "lucide-react";
import type { ReactNode } from "react";

export type StateKind = "empty" | "held" | "delayed" | "unavailable" | "error";

const ICON = { empty: Info, held: Info, unavailable: Info, delayed: Clock, error: CircleAlert } as const;

export function StateNote({
  kind,
  children,
  className,
}: {
  kind: StateKind;
  children: ReactNode;
  className?: string;
}) {
  const Icon = ICON[kind];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-ink-2", className)} data-state-kind={kind}>
      <Icon aria-hidden className={cn("size-3.5 shrink-0", kind === "delayed" ? "text-warn" : "text-ink-3")} />
      <span>{children}</span>
    </span>
  );
}
