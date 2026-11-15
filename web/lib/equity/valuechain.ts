// web/lib/equity/valuechain.ts: the value chain card's model (VC-06, #191; Spec 02B §12.4, §12.7): what the Overview panel's flow
// and the sub-page's tables say, built from the rows `api.value_chain` and its sibling views return. The browser derives no
// figure: every number is a view column (a share in percent as filed, a count, a change, a restated comparative), and this file
// only picks, orders and words them. Every state is the view's (`ok`, `held`, `absent`, `zero`, `not_shown`), never a parsed reason.
// A share or a change is a fact the company filed, not a verdict: no colour, no Delta, none of §12.7's banned words.
import {
  EN_DASH,
  NOT_AVAILABLE,
  formatCount,
  dateInZone,
  formatCountChangeWords,
  formatDate,
  formatFiledShare,
  formatShareChangeWords,
} from "@/lib/format";
import type { RelatedPartyTopRow, ValueChainPeerRow, ValueChainProductRow, ValueChainRow } from "@/lib/queries/api";

/** The field policies this card reads (migration 0034; all seeded hidden). */
export const VC_VALUES_FIELD = "equity.brsr_values";
export const VC_GROUP_FIELD = "equity.group_companies";
export const VC_RPT_FIELD = "equity.rpt_counterparties";
export const VC_SEGMENTS_FIELD = "equity.segments";

export const VC_HELD = "Value chain held back until it passes review";
export const RPT_HELD = "Related-party counterparties held back until they pass review";
export const GROUP_HELD = "Group companies held back until they pass review";
export const SEGMENTS_HELD = "Segments held back until they pass review";
export const PEERS_HELD = "Peer comparison held back until the value chain passes review";

export const AS_REPORTED = "As reported by the company; not verified by this site";
/** The figures are the XBRL's; the company's PDF of the same report may differ (VC-06c, #433; Anand's ruling of 12 Nov 2026 on #191). */
export const XBRL_NOTE =
  "The figures are the ones the company tagged in the BRSR's XBRL file filed with the exchange. The company's PDF of the same report may round a figure differently, or say \"Not applicable\" where the XBRL file carries 0.";
/** What a restated note means, so a restatement missing here is not read as none (VC-06c, #433). */
export const RESTATED_NOTE =
  "A restatement is shown where the next year's XBRL file carries the earlier year's figure; a comparative that appears only in the next year's PDF is not shown.";
export const NOT_REPORTED = "Not reported in the company's BRSR";

/** The openness-of-business figures, the only ones the filing's assurance flag covers (Principle 1, Essential Indicator 9; decision 5 of #191). */
const OPENNESS = new Set(["trading_house_share", "trading_houses", "top10_trading_share", "dealer_share", "dealers", "top10_dealer_share", "rpt_purchases_share", "rpt_sales_share"]);
/** The fields whose zero reads "Filed as 0" (dealers and trading houses). */
const ZERO_AS_FILED = new Set(["trading_house_share", "trading_houses", "top10_trading_share", "dealer_share", "dealers", "top10_dealer_share"]);

const SOURCE_NAME: Record<string, string> = { nse: "NSE", bse: "BSE" };

/** "Source: the company's BRSR, filed on NSE" (or BSE). */
export function brsrSourceLine(sourceKey: string | null | undefined): string {
  return `Source: the company's BRSR, filed on ${SOURCE_NAME[(sourceKey ?? "nse").toLowerCase()] ?? "NSE"}`;
}

/** "Standalone, as reported in the BRSR". */
export function boundaryText(boundary: "standalone" | "consolidated"): string {
  return `${boundary === "standalone" ? "Standalone" : "Consolidated"}, as reported in the BRSR`;
}

/**
 * The day a BRSR was filed, as `YYYY-MM-DD` in India. The exchange's list gives a date, stored as that day's midnight IST
 * (`2026-07-19T18:30:00+00:00` is 20 Jul 2026), so the instant's UTC date is the day before: never slice the string (#191).
 */
export const filedDay = (at: string | null | undefined): string | null => (at ? dateInZone(at, "Asia/Kolkata") : null);

/** The https link of an exchange list page, else null (a stored URL that isn't https renders as text, #367). */
export const httpsOnly = (url: string | null | undefined): string | null => (url?.startsWith("https://") ? url : null);

/** Whether the panel can show anything: held back, nothing published, or the rows grouped by fiscal year, oldest first. */
export type ValueChainModel =
  | { kind: "held" }
  | { kind: "error" }
  | { kind: "none" }
  | { kind: "years"; years: YearCards[] };

export type YearCards = {
  label: string;
  fyStart: string;
  fyEnd: string;
  boundary: "standalone" | "consolidated";
  months: number;
  exchange: "NSE" | "BSE";
  sourceKey: string;
  filedAt: string | null;
  listUrl: string | null;
  cells: ValueChainRow[];
};

/** The rows grouped by fiscal year, oldest first, each year's cells in the card's field order. */
export function yearsOf(rows: readonly ValueChainRow[]): YearCards[] {
  const by = new Map<string, ValueChainRow[]>();
  for (const r of rows) by.set(r.fy_label, [...(by.get(r.fy_label) ?? []), r]);
  return [...by.values()]
    .map((cells) => {
      const sorted = [...cells].sort((a, b) => a.field_no - b.field_no);
      const f = sorted[0];
      return {
        label: f.fy_label, fyStart: f.fy_start, fyEnd: f.fy_end, boundary: f.boundary, months: f.months, exchange: f.exchange,
        sourceKey: f.source_key, filedAt: f.filed_at, listUrl: f.list_url, cells: sorted,
      };
    })
    .sort((a, b) => a.fyEnd.localeCompare(b.fyEnd));
}

/** The panel's reading of the rows: held back (policy hidden, or any gated column null), couldn't load, nothing published, or the years. */
export function valueChainModel(rows: readonly ValueChainRow[] | null | undefined, policyShown: boolean): ValueChainModel {
  if (!policyShown) return { kind: "held" };
  if (rows === null || rows === undefined) return { kind: "error" };
  if (rows.length === 0) return { kind: "none" };
  // A row without a state is a gated row: the policy read said shown, the view said hidden. Fail closed.
  if (rows.some((r) => r.state === null)) return { kind: "held" };
  return { kind: "years", years: yearsOf(rows) };
}

/** The fiscal year in a `?year=` value ("2025-26"), else the latest. */
export function pickYear(years: readonly YearCards[], asked: string | string[] | undefined): YearCards {
  const want = typeof asked === "string" ? `FY ${asked}` : null;
  return years.find((y) => y.label === want) ?? years[years.length - 1];
}

/** The `?year=` value of a fiscal year's label: "FY 2025-26" → "2025-26". */
export const yearParam = (label: string): string => label.replace(/^FY /, "");

/** The label of the year after: "FY 2025-26" → "FY 2026-27", "FY 2025" → "FY 2026". */
export function nextLabel(label: string): string {
  const range = /^FY (\d{4})-(\d{2})$/.exec(label);
  if (range) {
    const start = Number(range[1]) + 1;
    return `FY ${start}-${String((start + 1) % 100).padStart(2, "0")}`;
  }
  const single = /^FY (\d{4})$/.exec(label);
  return single ? `FY ${Number(single[1]) + 1}` : label;
}

const day = (iso: string): number => Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10)));
const DAY_MS = 86_400_000;

/** The year whose end is the day before this year's start: the one the view compares with. */
export function previousYear(years: readonly YearCards[], year: YearCards): YearCards | null {
  return years.find((y) => day(year.fyStart) - day(y.fyEnd) === DAY_MS) ?? null;
}

/** "15 months, 1 Jan 2023 – 31 Mar 2024" for a year that isn't 12 months; null for a 12-month year. */
export function monthsNote(year: Pick<YearCards, "months" | "fyStart" | "fyEnd">): string | null {
  if (year.months === 12) return null;
  return `${formatCount(year.months)} months, ${formatDate(year.fyStart)} ${EN_DASH} ${formatDate(year.fyEnd)}`;
}

/** A cell's value as the card writes it (a share as filed, a count grouped as India groups), or the state in short words. */
export function cellValue(cell: ValueChainRow): string {
  if (cell.state === "ok" || cell.state === "zero") {
    if (cell.value === null) return NOT_AVAILABLE;
    if (cell.state === "zero" && ZERO_AS_FILED.has(cell.field_key)) return "Filed as 0";
    return cell.kind === "share" ? formatFiledShare(cell.value) : formatCount(cell.value, "IN");
  }
  if (cell.state === "absent") return "Not reported";
  if (cell.state === "held") return "Held back";
  return "Not shown";
}

/** The full state sentence for a cell that has one (the explainer's StateNote), else null. */
export function cellNote(cell: ValueChainRow, family: string | null | undefined): string | null {
  if (cell.state === "absent") return `${cell.label}: ${NOT_REPORTED}`;
  if (cell.state === "held") return `${cell.label}: held back${cell.state_reason ? ` (${cell.state_reason})` : ""}`;
  if (cell.state === "not_shown") return `${cell.label}: not shown, because the share it is a part of is 0 or not reported`;
  if (cell.state === "zero") {
    if (ZERO_AS_FILED.has(cell.field_key)) {
      return `${cell.label}: Filed as 0${family === "lender" || family === "insurer" ? " (not applicable to banks and insurers)" : ""}`;
    }
    return `${cell.label}: ${cell.kind === "share" ? "0%" : "0"}, as filed`;
  }
  return null;
}

/** "Restated in the FY 2026-27 BRSR to 9.8%", for a value the next year's filing restated; null otherwise. */
export function restatedNote(cell: ValueChainRow): string | null {
  if (!cell.restated || cell.next_comparative === null) return null;
  const to = cell.kind === "share" ? formatFiledShare(cell.next_comparative) : formatCount(cell.next_comparative, "IN");
  return `Restated in the ${nextLabel(cell.fy_label)} BRSR to ${to}`;
}

/** The change line under a figure: "up 2.4 pp on FY 2024-25", the boundary-change note, or null where there is nothing to say. */
export function changeLine(cell: ValueChainRow, year: YearCards, years: readonly YearCards[]): string | null {
  if (cell.change !== null) {
    const prev = previousYear(years, year);
    const words = cell.kind === "share" ? formatShareChangeWords(cell.change) : formatCountChangeWords(cell.change);
    return `${words} on ${prev?.label ?? "the year before"}`;
  }
  if (cell.change_reason === "boundary_change") {
    const prev = previousYear(years, year);
    if (prev) return `${cap(prev.boundary)} in ${prev.label}, ${year.boundary} in ${year.label}: not compared`;
    return "Not compared: the boundary changed";
  }
  if (cell.change_reason === "not_12_months") return "Not compared: a year of other than 12 months is involved";
  if (cell.change_reason === "previous_value_unavailable") return "Not compared: the earlier year's value is not shown";
  return null;
}

const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

/** The filing's assurance line for the openness figures, where it states one: "Reasonable assurance, as stated in the BRSR". */
export function assuranceLine(cells: readonly ValueChainRow[]): string | null {
  const c = cells.find((x) => OPENNESS.has(x.field_key) && x.assured === true);
  if (!c) return null;
  if (c.assurance === "reasonable") return "Reasonable assurance, as stated in the BRSR";
  if (c.assurance === "limited") return "Limited assurance, as stated in the BRSR";
  return "Assurance, as stated in the BRSR";
}

// ---------------------------------------------------------------------------------------------------------------
// The flow

/** One figure on the flow: its label, what it shows, a state note and its detail lines. Strings only, so the client component takes it as is. */
export type FlowItem = {
  key: string;
  label: string;
  display: string;
  /** True when `display` is a state in words rather than a figure. */
  isState: boolean;
  details: string[];
  change: string | null;
  restated: string | null;
};

export type FlowProduct = { name: string; share: string };

export type FlowTableRow = { side: string; label: string; value: string; itemRef: string };

export type Flow = {
  company: string;
  fy: string;
  boundary: string;
  suppliers: FlowItem[];
  products: FlowProduct[];
  productsState: string | null;
  customers: FlowItem[];
  group: FlowItem[];
  /** "Inputs: …" read as sentences, for screen readers. */
  aria: string;
  table: FlowTableRow[];
};

/** A detail line under a figure; `one` is the label for a count of exactly 1 ("1 country"). */
type Spec = { key: string; label: string; details?: { key: string; label: string; one?: string }[] };

const SUPPLIERS: Spec[] = [
  { key: "within_india_share", label: "Inputs from within India" },
  { key: "msme_share", label: "Inputs from MSMEs and small producers" },
  { key: "trading_house_share", label: "Purchases through trading houses", details: [{ key: "trading_houses", label: "trading houses", one: "trading house" }, { key: "top10_trading_share", label: "top 10 trading houses" }] },
];
const CUSTOMERS: Spec[] = [
  { key: "dealer_share", label: "Sales through dealers and distributors", details: [{ key: "dealers", label: "dealers and distributors", one: "dealer or distributor" }, { key: "top10_dealer_share", label: "top 10 dealers and distributors" }] },
  { key: "exports_share", label: "Exports, share of turnover", details: [{ key: "countries_served", label: "countries", one: "country" }, { key: "states_served", label: "states", one: "state" }] },
];
const GROUP: Spec[] = [
  { key: "rpt_purchases_share", label: "Purchases from related parties" },
  { key: "rpt_sales_share", label: "Sales to related parties" },
];

/** The flow of one year: suppliers, the company with its top three products, customers, and the group, from that year's cells. */
export function flowOf(
  year: YearCards,
  products: readonly ValueChainProductRow[],
  opts: { company: string; family: string | null | undefined; years?: readonly YearCards[] },
): Flow {
  const byKey = new Map(year.cells.map((c) => [c.field_key, c]));
  const itemOf = (spec: Spec): FlowItem | null => {
    const cell = byKey.get(spec.key);
    if (!cell || cell.state === "not_shown") return null;
    const details: string[] = [];
    for (const d of spec.details ?? []) {
      const dc = byKey.get(d.key);
      if (!dc || dc.state === "not_shown") continue;
      // A detail that is itself unreadable says so in short words; a figure reads "4,480 dealers and distributors".
      const shownAsCount = dc.kind === "count" && (dc.state === "ok" || (dc.state === "zero" && !ZERO_AS_FILED.has(dc.field_key)));
      const change = opts.years ? changeLine(dc, year, opts.years) : null;
      // A restated detail says so beside its figure, as the main figures do (#191's page check: Trent's 13 trading houses had no note).
      const notes = [change, restatedNote(dc)].filter((n): n is string => n !== null);
      const noun = shownAsCount && dc.value === 1 && d.one ? d.one : d.label;
      details.push(`${shownAsCount ? `${cellValue(dc)} ${noun}` : `${d.label}: ${cellValue(dc)}`}${notes.length ? ` (${notes.join("; ")})` : ""}`);
    }
    return {
      key: spec.key,
      label: spec.label,
      display: cellValue(cell),
      isState: cell.state !== "ok" && !(cell.state === "zero" && !ZERO_AS_FILED.has(cell.field_key)),
      details,
      change: opts.years ? changeLine(cell, year, opts.years) : null,
      restated: restatedNote(cell),
    };
  };
  const list = (specs: Spec[]) => specs.map(itemOf).filter((i): i is FlowItem => i !== null);
  const suppliers = list(SUPPLIERS);
  const customers = list(CUSTOMERS);
  const group = list(GROUP);
  const top = [...products]
    .filter((p) => p.fy_label === year.label && p.description !== null && p.share_of_turnover_pct !== null)
    .sort((a, b) => (b.share_of_turnover_pct ?? 0) - (a.share_of_turnover_pct ?? 0) || a.line_no - b.line_no)
    .slice(0, 3)
    .map((p) => ({ name: p.description as string, share: formatFiledShare(p.share_of_turnover_pct) }));
  const productsState = top.length === 0 ? NOT_REPORTED : null;
  const sentence = (i: FlowItem) => `${i.label}: ${i.display}${i.details.length ? ` (${i.details.join("; ")})` : ""}`;
  const aria = [
    `Value chain, ${year.label}, ${boundaryText(year.boundary)}.`,
    `Inputs. ${suppliers.map(sentence).join(". ") || NOT_REPORTED}.`,
    `${opts.company}. ${top.length ? `Products by share of turnover: ${top.map((p) => `${p.name} ${p.share}`).join("; ")}` : `Products: ${NOT_REPORTED}`}.`,
    `Customers. ${customers.map(sentence).join(". ") || NOT_REPORTED}.`,
    `Within the group. ${group.map(sentence).join(". ") || NOT_REPORTED}.`,
  ].join(" ");
  const table: FlowTableRow[] = [];
  const push = (side: string, specs: Spec[]) => {
    for (const spec of specs) {
      for (const k of [spec.key, ...(spec.details ?? []).map((d) => d.key)]) {
        const c = byKey.get(k);
        if (c) table.push({ side, label: c.label, value: cellValue(c), itemRef: c.item_ref });
      }
    }
  };
  push("Suppliers", SUPPLIERS);
  top.forEach((p, i) => table.push({ side: "The company", label: `Product ${i + 1}: ${p.name}`, value: p.share, itemRef: "A, products by share of turnover" }));
  push("Customers", CUSTOMERS);
  push("Within the group", GROUP);
  return { company: opts.company, fy: year.label, boundary: boundaryText(year.boundary), suppliers, products: top, productsState, customers, group, aria, table };
}

/** Every full state sentence of a year's cells (held, absent, zero, not shown), for the explainer. */
export function stateNotes(year: YearCards, family: string | null | undefined): string[] {
  return year.cells.map((c) => cellNote(c, family)).filter((n): n is string => n !== null);
}

// ---------------------------------------------------------------------------------------------------------------
// The sub-page's related-party and peer tables

/** The period a counterparty list covers, with what it doesn't: a six-month sum is never called a year (22:15 IST note on #191). */
export type RptPeriod = { from: string; to: string; fullYear: boolean };

/** Whether a related-party span is a whole fiscal year: at least 350 days, as the segments view treats a year. */
export function rptPeriod(rows: readonly RelatedPartyTopRow[]): RptPeriod | null {
  if (rows.length === 0) return null;
  const from = rows.map((r) => r.period_from).sort()[0];
  const to = rows.map((r) => r.period_to).sort().at(-1) as string;
  return { from, to, fullYear: (day(to) - day(from)) / DAY_MS >= 350 };
}

/** "1 Apr 2025 – 30 Sep 2025". */
export const periodText = (p: RptPeriod): string => `${formatDate(p.from)} ${EN_DASH} ${formatDate(p.to)}`;

/** The latest fiscal year in the related-party rows, and its rows. */
export function latestRpt(rows: readonly RelatedPartyTopRow[]): { label: string; rows: RelatedPartyTopRow[] } | null {
  if (rows.length === 0) return null;
  const year = Math.max(...rows.map((r) => r.fy_end_year));
  const mine = rows.filter((r) => r.fy_end_year === year).sort((a, b) => a.rank - b.rank);
  return { label: mine[0].fy_label, rows: mine };
}

// ---------------------------------------------------------------------------------------------------------------
// The sub-page's tables (strings only: the client tables take them as they are)

export type FieldsRow = { key: string; label: string; itemRef: string; cells: { year: string; text: string; marks: number[] }[] };
export type FieldsTableModel = { years: string[]; rows: FieldsRow[]; footnotes: string[] };

/** "Every field": one row per card field, one column per fiscal year, the restated and held notes as numbered footnotes. */
export function fieldsTable(years: readonly YearCards[], family: string | null | undefined): FieldsTableModel {
  const footnotes: string[] = [];
  const mark = (text: string): number => {
    const at = footnotes.indexOf(text);
    if (at >= 0) return at + 1;
    footnotes.push(text);
    return footnotes.length;
  };
  const first = years[years.length - 1];
  const meta: FieldsRow[] = [
    { key: "_boundary", label: "Boundary", itemRef: "Section A", cells: years.map((y) => ({ year: y.label, text: cap(y.boundary), marks: [] })) },
    {
      key: "_filed",
      label: "Filed",
      itemRef: "The exchange's BRSR list",
      cells: years.map((y) => ({ year: y.label, text: y.filedAt ? formatDate(filedDay(y.filedAt)) : NOT_AVAILABLE, marks: [] })),
    },
  ];
  const fields = first.cells.map((c) => c.field_key);
  const rows = fields.map((key): FieldsRow => {
    const ref = first.cells.find((c) => c.field_key === key) as ValueChainRow;
    return {
      key,
      label: ref.label,
      itemRef: ref.item_ref,
      cells: years.map((y) => {
        const c = y.cells.find((x) => x.field_key === key);
        if (!c) return { year: y.label, text: "Not reported", marks: [] };
        const marks: number[] = [];
        const restated = restatedNote(c);
        if (restated) marks.push(mark(`${c.label}, ${y.label}: ${restated}`));
        if (c.state === "held") marks.push(mark(cellNote(c, family) ?? ""));
        const note = monthsNote(y);
        if (note && key === first.cells[0].field_key) marks.push(mark(`${y.label} is ${note}; no change is computed against it`));
        return { year: y.label, text: cellValue(c), marks };
      }),
    };
  });
  return { years: years.map((y) => y.label), rows: [...meta, ...rows], footnotes };
}

export type RptRow = { key: string; rank: string; counterparty: string; relationship: string; value: string };

/** One list of the top five: rows with the value in rupees as crore. `amount` formats a value in the company's currency. */
export function rptRows(rows: readonly RelatedPartyTopRow[], type: RelatedPartyTopRow["txn_type"], amount: (v: number | null) => string): RptRow[] {
  return rows
    // A value summed to 0 or below (a net zero or a net reversal once the half-years are added) is not one of the largest (VC-06f, #518).
    .filter((r) => r.txn_type === type && r.counterparty !== null && (r.value === null || r.value > 0))
    .sort((a, b) => a.rank - b.rank)
    .map((r) => ({ key: `${type}|${r.rank}`, rank: formatCount(r.rank), counterparty: r.counterparty as string, relationship: r.relationship ?? NOT_AVAILABLE, value: amount(r.value) }));
}

export type PeerCell = { text: string };
export type PeerRowModel = { key: string; company: string; self: boolean; boundary: string; sameBasis: boolean; cells: Record<string, string> };
export type PeersModel = { fields: { key: string; label: string }[]; rows: PeerRowModel[]; median: { n: number; cells: Record<string, string> } | null; year: string | null };

const PEER_FIELDS = ["within_india_share", "msme_share", "dealer_share", "rpt_purchases_share", "rpt_sales_share"] as const;
const PEER_SHORT: Record<(typeof PEER_FIELDS)[number], string> = {
  within_india_share: "Within India",
  msme_share: "MSMEs",
  dealer_share: "Via dealers",
  rpt_purchases_share: "RPT purchases",
  rpt_sales_share: "RPT sales",
};

/** The peers table: one row per company in the peer group (no ranking: the order is the company's name), the median only where the view gives one. */
export function peersModel(rows: readonly ValueChainPeerRow[]): PeersModel {
  const wanted = new Set<string>(PEER_FIELDS);
  const mine = rows.filter((r) => wanted.has(r.field_key));
  const bySlug = new Map<string, ValueChainPeerRow[]>();
  for (const r of mine) bySlug.set(r.peer_slug, [...(bySlug.get(r.peer_slug) ?? []), r]);
  const out: PeerRowModel[] = [...bySlug.entries()]
    .map(([slug, list]) => {
      const f = list[0];
      const cells: Record<string, string> = {};
      for (const k of PEER_FIELDS) {
        const c = list.find((x) => x.field_key === k);
        cells[k] = !c ? "Not reported" : c.state === "ok" || c.state === "zero" ? (c.value === null ? NOT_AVAILABLE : formatFiledShare(c.value)) : c.state === "absent" ? "Not reported" : c.state === "held" ? "Held back" : "Not shown";
      }
      return { key: slug, company: f.peer_short_name ?? f.peer_name, self: f.is_self, boundary: cap(f.boundary), sameBasis: f.same_basis, cells };
    })
    .sort((a, b) => a.company.localeCompare(b.company));
  // One median per field, from the view's own columns; all five agree on n only by luck, so the row shows each field's own n when they differ.
  const med: Record<string, string> = {};
  let n = 0;
  for (const k of PEER_FIELDS) {
    const c = mine.find((x) => x.field_key === k && x.median !== null && (x.median_n ?? 0) >= 3);
    if (c) {
      med[k] = formatFiledShare(Number(c.median));
      n = Math.max(n, c.median_n ?? 0);
    }
  }
  const any = Object.keys(med).length > 0;
  for (const k of PEER_FIELDS) if (any && !(k in med)) med[k] = "Fewer than 3 peers";
  return {
    fields: PEER_FIELDS.map((k) => ({ key: k, label: PEER_SHORT[k] })),
    rows: out,
    median: any ? { n, cells: med } : null,
    year: mine[0]?.fy_label ?? null,
  };
}
