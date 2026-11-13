"use client";

// "How members voted over time" (#508; Design 01 §9, Pass 2): one panel per bank, one dot strip per held rate
// decision stacked by date (newest at the top), on one bps axis shared by the bank's strips. A dot is drawn exactly as
// VoteDotPlot draws it (position is the vote; equal votes stack upward; a dissent that isn't about the rate is
// hollow at the decision), only smaller, so a long history fits. Hovering or focusing a dot, or choosing a member in
// the select, rings that member's dots in every strip with the white focus colour and dims the rest: no new hue, and
// no colour for a vote or a dissent. "View as table" swaps in the member × meeting grid (DataTable, member column
// sticky), each cell the vote as the meeting page words it, a dissent a "Dissent" tag.
// Keyboard: the dots are buttons but, with a thousand of them, only a chosen member's are in the tab order; the
// select and the table are the keyboard route to every vote. A meeting whose votes aren't all stored shows the tally
// in words and no dots. Plain HTML/CSS positioned by percentages, so it reflows to 320 px without sideways scroll.
import { ChartScatter, Table2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Panel } from "@/components/data/Panel";
import { StateNote } from "@/components/data/StateNote";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { NOT_AVAILABLE, formatAxisChange, formatTableDate } from "@/lib/format";
import { dotEmphasis, type BankVotes, type StripRow } from "@/lib/policy/votes-over-time";

const PITCH = 5; // px between stacked dots
const HIT = 24; // px hit area
const ROW_MIN = 36; // px: the date and the decision on two lines

const SELECT = "h-8 rounded-md border border-line-input bg-raised px-2 text-read md:text-data text-ink-1 hover:border-ink-3";

function tickLabel(bps: number): string {
  return bps === 0 ? "Hold" : formatAxisChange(bps);
}

type Cell = { name: string; key: string };

export function VotesOverTimePanel({ data, footer, refresh }: { data: BankVotes; footer: ReactNode; refresh?: readonly string[] }) {
  const fed = data.bank === "Fed";
  const [asTable, setAsTable] = useState(false);
  const [member, setMember] = useState("all");
  const [hover, setHover] = useState<string | null>(null);
  const active = member !== "all" ? member : hover;
  const title = `${data.bank}: how members voted over time`;
  const empty = data.rows.length === 0;

  return (
    <Panel
      title={title}
      titleId={`panel-votes-over-time-${fed ? "fed" : "rbi"}`}
      subtitle="How has each member voted, meeting by meeting, and when did dissent appear?"
      market={fed ? "US" : "IN"}
      variant="flush"
      span={6}
      footer={footer}
      refresh={refresh}
    >
      {empty ? (
        <div className="p-3">
          <StateNote kind="unavailable">No decisions are on the site yet.</StateNote>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-line px-3 py-2">
            <label className="flex items-center gap-1.5 text-meta text-ink-2">
              Member
              <select className={SELECT} value={member} onChange={(e) => setMember(e.target.value)}>
                <option value="all">All members</option>
                {data.members.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => setAsTable((v) => !v)}
              aria-pressed={asTable}
              aria-label={asTable ? "View as chart" : "View as table"}
              title={asTable ? "View as chart" : "View as table"}
              className="grid size-7 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-active hover:text-ink-1"
            >
              {asTable ? <ChartScatter aria-hidden className="size-4" /> : <Table2 aria-hidden className="size-4" />}
            </button>
          </div>
          {asTable ? <Grid data={data} active={active} /> : <Strips data={data} fed={fed} active={active} member={member} onHover={setHover} />}
        </>
      )}
    </Panel>
  );
}

function Strips({
  data,
  fed,
  active,
  member,
  onHover,
}: {
  data: BankVotes;
  fed: boolean;
  active: string | null;
  member: string;
  onHover: (key: string | null) => void;
}) {
  const { min, max, ticks } = data.axis;
  const span = max - min;
  const pos = (bps: number) => `${((bps - min) / span) * 100}%`;
  return (
    <div
      className="relative max-h-(--table-max-h) overflow-auto focus-visible:-outline-offset-2"
      role="region"
      aria-label={`${data.bank} votes by meeting, newest first`}
      tabIndex={0}
    >
      <div className="sticky top-0 z-30 flex border-b border-line-strong bg-raised text-meta text-ink-3">
        <div className="w-24 shrink-0 px-3 py-1 font-medium text-ink-2">Meeting</div>
        <div className="relative my-1 mr-4 ml-0 flex-1" aria-hidden>
          <div className="h-4">
            {ticks
              .filter((t) => t % 50 === 0)
              .map((t) => (
                <span key={t} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: pos(t) }}>
                  {tickLabel(t)}
                </span>
              ))}
          </div>
          <div className="flex justify-between">
            <span>← cut</span>
            <span>hike →</span>
          </div>
        </div>
      </div>
      <ol>
        {data.rows.map((r) => (
          <Strip key={r.slug} r={r} ticks={ticks} pos={pos} fed={fed} active={active} member={member} onHover={onHover} />
        ))}
      </ol>
    </div>
  );
}

function Strip({
  r,
  ticks,
  pos,
  fed,
  active,
  member,
  onHover,
}: {
  r: StripRow;
  ticks: number[];
  pos: (bps: number) => string;
  fed: boolean;
  active: string | null;
  member: string;
  onHover: (key: string | null) => void;
}) {
  const fill = fed ? "bg-series-1" : "bg-series-2";
  const edge = fed ? "border-series-1" : "border-series-2";
  const height = r.kind === "dots" ? Math.max(ROW_MIN, r.maxLevel * PITCH + HIT) : ROW_MIN;
  return (
    <li className="flex border-b border-line last:border-b-0" style={{ minHeight: height }}>
      <div className="w-24 shrink-0 px-3 py-1 text-meta">
        <Link href={r.href} className="block font-mono text-ink-1 underline">
          {formatTableDate(r.date)}
        </Link>
        <span className="block text-ink-3">{r.decision === null ? NOT_AVAILABLE : `${formatAxisChange(r.decision)} bps`}</span>
      </div>
      {r.kind === "note" ? (
        <p className="flex-1 self-center py-1 pr-3 text-meta text-ink-2">{r.text}</p>
      ) : (
        <div className="relative mr-4 flex-1" role="group" aria-label={`${formatTableDate(r.date)}: ${r.summary}`}>
          {ticks.map((t) => (
            <span key={t} aria-hidden className="absolute inset-y-0 w-px bg-line" style={{ left: pos(t) }} />
          ))}
          <span aria-hidden className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-ink-1" style={{ left: pos(r.decision) }} />
          {r.dots.map((d) => {
            const emphasis = dotEmphasis(active, d.key);
            const mine = emphasis === "mine";
            const dim = emphasis === "dim";
            return (
              <Popover key={d.key}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    tabIndex={member === d.key ? 0 : -1}
                    aria-label={`${d.member}, ${formatTableDate(r.date)}: ${d.lines.join("; ")}${d.hollow ? " (hollow dot: dissent not on the rate)" : ""}`}
                    onMouseEnter={() => onHover(d.key)}
                    onMouseLeave={() => onHover(null)}
                    onFocus={() => onHover(d.key)}
                    onBlur={() => onHover(null)}
                    className={`absolute grid -translate-x-1/2 place-items-center rounded-sm ${mine ? "z-20" : "z-10"}`}
                    style={{ left: pos(d.at), bottom: d.level * PITCH, width: HIT, height: HIT }}
                  >
                    <span
                      aria-hidden
                      className={`block size-1.25 rounded-full ${
                        mine ? "ring-highlight ring-2 opacity-100" : "ring-panel ring-1 opacity-100"
                      } ${d.hollow ? `border ${edge} bg-panel` : fill} ${dim ? "opacity-30" : ""}`}
                    />
                  </button>
                </PopoverTrigger>
                <PopoverContent className="w-auto max-w-90">
                  <p className="text-data font-medium text-ink-1">{d.member}</p>
                  <p className="text-meta text-ink-3">{formatTableDate(r.date)}</p>
                  {d.lines.map((l) => (
                    <p key={l} className="text-meta text-ink-2">
                      {l}
                    </p>
                  ))}
                  {d.hollow && <p className="text-meta text-ink-2">Hollow dot: a dissent that isn&apos;t about the rate.</p>}
                  {d.href && (
                    <p className="mt-2 text-meta">
                      <a href={d.href} className="text-ink-1 underline">
                        Open the cited passage
                      </a>
                    </p>
                  )}
                </PopoverContent>
              </Popover>
            );
          })}
        </div>
      )}
    </li>
  );
}

function Grid({ data, active }: { data: BankVotes; active: string | null }) {
  const columns = useMemo<Column<Cell>[]>(
    () => [
      {
        id: "member",
        header: "Member",
        cell: (r) => <span className={r.key === active ? "font-medium underline" : undefined}>{r.name}</span>,
      },
      ...data.meetings.map(
        (m): Column<Cell> => ({
          id: m.slug,
          header: formatTableDate(m.date),
          align: "right",
          cell: (r) => {
            const c = data.cells.get(m.slug)?.get(r.key);
            if (!c) return <span aria-label="No vote recorded">{NOT_AVAILABLE}</span>;
            return (
              <span className="inline-flex flex-wrap items-center justify-end gap-x-1.5">
                <span>{c.vote}</span>
                {data.stance && c.stance && <span className="text-ink-3">{c.stance}</span>}
                {c.dissent && <span className="rounded-sm border border-line-strong px-1.5 text-meta text-ink-2">Dissent</span>}
              </span>
            );
          },
        }),
      ),
    ],
    [data, active],
  );
  const rows = useMemo<Cell[]>(() => data.members.map((m) => ({ key: m.key, name: m.name })), [data.members]);
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.key}
      caption={`${data.bank} members' votes by meeting, newest first; the vote in basis points${data.stance ? " and the stance vote" : ""}`}
      scroll
      empty={<StateNote kind="unavailable">No member votes are on the site yet.</StateNote>}
    />
  );
}
