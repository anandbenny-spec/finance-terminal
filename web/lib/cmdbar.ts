// web/lib/cmdbar.ts: the command bar's plain parts (K-07, #585; Spec 07 §3, §4, §7; Design 01 §4.6). No React here, so every
// rule is a unit test (lib/cmdbar.test.ts) and the box (components/cmdbar/) only wires them to the page.
//
// - **The gate** (Spec 07 §3, N1): the box renders only while `site.command_bar` has a shown or labelled row in
//   core.field_policy. No row (the state until K-11's judge passes and Anand flips) means hidden: the top bar is unchanged.
// - **Plain search** (Spec 07 §1, §4 step 1): in the browser, over `api.search_index` through the Data API with the browser key
//   (CLAUDE.md §2: the frontend reads published data straight from `api` views; RLS decides what a visitor sees). Plain
//   `fetch`, so supabase-js isn't loaded for every visitor (lib/supabase/browser.ts). Each page asks for an exact count and is
//   checked against it (CLAUDE.md §6: the Data API truncates at 1,000 rows without an error). No AI, no request to /api.
// - **The answer stream** (Spec 07 §4, "Answer shape"): `POST /api/cmdbar/ask` streams newline-delimited JSON, status lines
//   then one final object. Anything off its shape is "couldn't get an answer", never a raw error (CLAUDE.md §4).
// - **Cells** arrive as exact decimal strings and ISO dates (`ft.cmdbar.route.cell`); they're shown through lib/format.ts,
//   never rounded, so the figure on screen is the row's figure (CLAUDE.md §1).
// - **A document answer's chips** (K-08, #586): `documentAnswer` turns the route's verified references into CitationChip's
//   citations; an RBI document is named (title, date, press-release number) and never linked to RBI's pages (CLAUDE.md §1).
import { companyHref } from "@/lib/equity/company";
import {
  NOT_AVAILABLE,
  dateInZone,
  formatCheckedTime,
  formatDate,
  formatExactDecimal,
  formatTableDate,
  type Market,
} from "@/lib/format";
import { cutQuote, documentHref, type Citation } from "@/lib/policy/ai";
import { meetingHref } from "@/lib/policy/decisions";
import type { FieldPolicyRow } from "@/lib/queries/api";
import { NAV, visibleGroups } from "@/lib/shell/nav";

// ------------------------------------------------------------------------------------------------ the gate (Spec 07 §3)

/** The field key that shows the box. No core.field_policy row means hidden (fails closed, as the database does). */
export const CMDBAR_FIELD = "site.command_bar";

/** Whether the box is published: a row for `site.command_bar` with status `shown` or `labelled`. */
export function commandBarShown(policies: readonly FieldPolicyRow[] | null): boolean {
  const row = policies?.find((p) => p.field_key === CMDBAR_FIELD) ?? null;
  return row !== null && (row.status === "shown" || row.status === "labelled");
}

// ------------------------------------------------------------------------------------------------ words (Design 01 §4.6)

export const PLACEHOLDER = "Search meetings and companies, or ask about the data";
export const PAUSED_TEXT = "AI questions are paused for now; search still works.";
export const CANT_TEXT = "That isn't in this site's data.";
export const NOT_FOUND_TEXT = "Not found in the stored Fed and RBI documents.";
/** The client's own state for a failed request or a stream off its shape: never the error itself. */
export const ERROR_TEXT = "Couldn't get an answer. Try again; search still works.";
export const AI_SUMMARY = "AI summary";
/** The line over a document answer's documents (K-08). */
export const CITED_DOCUMENTS = "Cited documents";
export const TRY = "Try";
/** The first status line, shown as soon as a question is sent (the route's own lines follow). */
export const FIRST_STATUS = "Understanding the question…";

/** The last row while typing: "Ask: '…'" (Design 01 §4.6). */
export function askLabel(question: string): string {
  return `Ask: '${question.trim()}'`;
}

/** The ⌘K tag: ⌘K on Apple platforms, Ctrl K elsewhere (Design 01 §1.11, §4.6). */
export function shortcutLabel(platform: string | undefined): string {
  return platform && /mac|iphone|ipad|ipod/i.test(platform) ? "⌘K" : "Ctrl K";
}

/** Whether a key press is the one shortcut (⌘K or Ctrl K), with no other modifier. */
export function isShortcut(e: { key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey: boolean }): boolean {
  return e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey;
}

/** The keys cmdk's list takes for itself (it calls preventDefault on them), so a link or chip in an answer would lose Enter. */
export const LIST_KEYS: ReadonlySet<string> = new Set(["Enter", "ArrowDown", "ArrowUp", "Home", "End"]);

/**
 * Inside an answer (and the citation popovers and sheets opened from it, which React's events pass through), Enter opens the
 * focused link or chip and the arrows scroll, as anywhere on the site: the keys stop here, before cmdk's list handler. The
 * input keeps cmdk's model (arrows move, Enter selects); ⌘K / Ctrl K and Esc aren't stopped.
 */
export function keepAnswerKeys(e: { key: string; stopPropagation: () => void }) {
  if (LIST_KEYS.has(e.key)) e.stopPropagation();
}

// ------------------------------------------------------------------------------------------------ plain search

/** One `api.search_index` row (20261020000600_api_views.sql). */
export type SearchRow = {
  item_type: "entity" | "event";
  subtype: string;
  slug: string;
  title: string;
  parent_slug: string | null;
  on_date: string | null;
};

/** The rows that have a page: the two banks (the tracker), companies, held meetings and earnings calls. */
export const SEARCH_SUBTYPES = ["central_bank", "company", "policy_meeting", "earnings_call"] as const;
export const SEARCH_COLUMNS = "item_type,subtype,slug,title,parent_slug,on_date";
/** Rows per page; "Show more" asks for the next page. */
export const SEARCH_PAGE = 20;
/** A search needs this many characters (one letter would list most of the index). */
export const MIN_TERM = 2;

export type SearchGroup = "Pages" | "Companies" | "Meetings" | "Earnings calls";
export const GROUP_ORDER: readonly SearchGroup[] = ["Pages", "Companies", "Meetings", "Earnings calls"];

/** A result the box lists: where it goes and what it says. */
export type SearchHit = { key: string; group: SearchGroup; title: string; detail: string | null; href: string };

/**
 * The search term as the query may carry it: letters, digits, spaces and `&.'-` only, whitespace collapsed. Anything else
 * (PostgREST's `,()*"`, LIKE's `%_`, a backslash) becomes a space, so a visitor's text can't change the filter's shape.
 * Null when fewer than MIN_TERM characters are left.
 */
export function searchTerm(input: string): string | null {
  const t = input
    .normalize("NFKC")
    .replace(/[^\p{L}\p{N} &.'-]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
  return t.length >= MIN_TERM ? t.slice(0, 80) : null;
}

/** The Data API query for one page of results: entities by name or slug; events on or before `today` by title or slug. */
export function searchQuery(term: string, today: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(today)) throw new RangeError(`searchQuery: today must be YYYY-MM-DD, got ${today}`);
  const like = (column: string) => `${column}.ilike."*${term}*"`;
  const entity = `and(item_type.eq.entity,or(${like("title")},${like("slug")}))`;
  const event = `and(item_type.eq.event,on_date.lte.${today},or(${like("title")},${like("slug")},${like("parent_slug")}))`;
  const params = new URLSearchParams();
  params.set("select", SEARCH_COLUMNS);
  params.set("subtype", `in.(${SEARCH_SUBTYPES.join(",")})`);
  params.set("or", `(${entity},${event})`);
  params.set("order", "item_type.asc,on_date.desc.nullsfirst,title.asc,slug.asc");
  return params.toString();
}

/**
 * The Data API's URL and the browser key (public by design: CLAUDE.md §4), read as literals so Next puts them in the browser's
 * bundle; null when this build has none. lib/supabase/public.ts reads the same two, but importing it would load supabase-js.
 */
export function searchConfig(): { url: string; key: string } | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  return url && key ? { url: url.replace(/\/+$/, ""), key } : null;
}

export class SearchError extends Error {}

export type SearchPage = { rows: SearchRow[]; total: number };

/** A Content-Range header's total (`0-19/123`, `*\/0`); null without one. */
export function rangeTotal(header: string | null): number | null {
  const m = header ? /\/(\d+)$/.exec(header.trim()) : null;
  return m ? Number(m[1]) : null;
}

/**
 * One page of plain-search results, from row `from`, checked against the exact count: a page holding fewer rows than the count
 * says it should is an error, never a short list shown as complete.
 */
export async function searchIndex(
  term: string,
  from: number,
  opts: { url: string; key: string; today: string; fetch?: typeof fetch; signal?: AbortSignal },
): Promise<SearchPage> {
  const to = from + SEARCH_PAGE - 1;
  const res = await (opts.fetch ?? fetch)(`${opts.url}/rest/v1/search_index?${searchQuery(term, opts.today)}`, {
    headers: {
      apikey: opts.key,
      Authorization: `Bearer ${opts.key}`,
      "Accept-Profile": "api",
      Prefer: "count=exact",
      "Range-Unit": "items",
      Range: `${from}-${to}`,
    },
    signal: opts.signal,
  });
  if (!res.ok) throw new SearchError(`search_index answered ${res.status}`);
  const total = rangeTotal(res.headers.get("content-range"));
  if (total === null) throw new SearchError("search_index carried no exact count");
  const rows = (await res.json()) as unknown;
  if (!Array.isArray(rows)) throw new SearchError("search_index answered off its shape");
  const expected = Math.max(0, Math.min(SEARCH_PAGE, total - from));
  if (rows.length !== expected) throw new SearchError(`search_index gave ${rows.length} rows where ${expected} were due`);
  return { rows: rows as SearchRow[], total };
}

const MEETING_DATE = /(\d{4}-\d{2}-\d{2})$/;

/** The page a search_index row (or the route's page answer, which is one) opens; null when it has none. */
export function pageHref(row: Pick<SearchRow, "item_type" | "subtype" | "slug" | "parent_slug">): string | null {
  if (row.item_type === "entity") {
    if (row.subtype === "central_bank" && (row.slug === "fed" || row.slug === "rbi")) return "/policy";
    if (row.subtype === "company" && /^[a-z0-9-]{1,40}$/.test(row.slug)) return companyHref(row.slug, "overview");
    return null;
  }
  if (row.subtype === "policy_meeting" && (row.parent_slug === "fed" || row.parent_slug === "rbi")) {
    const date = MEETING_DATE.exec(row.slug)?.[1];
    return date ? meetingHref(row.parent_slug === "fed" ? "Fed" : "RBI", date) : null;
  }
  if (row.subtype === "earnings_call" && row.parent_slug && /^[a-z0-9-]{1,40}$/.test(row.slug)) {
    return companyHref(row.parent_slug, "calls", row.slug);
  }
  return null;
}

/** A search_index row as a result: its group, title, a date where it has one, and its page. */
export function hitOf(row: SearchRow): SearchHit | null {
  const href = pageHref(row);
  if (!href) return null;
  const key = `${row.item_type}:${row.slug}`;
  if (row.subtype === "central_bank") return { key, group: "Pages", title: row.title, detail: "Fed vs RBI", href };
  if (row.subtype === "company") return { key, group: "Companies", title: row.title, detail: null, href };
  if (row.subtype === "policy_meeting") {
    const date = MEETING_DATE.exec(row.slug)![1];
    return { key, group: "Meetings", title: row.title, detail: formatDate(date), href };
  }
  return { key, group: "Earnings calls", title: row.title, detail: row.on_date ? formatDate(row.on_date) : null, href };
}

/** The site's own pages a term names (a visitor's menu items and their section links), matched on their labels. */
export function pageHits(term: string | null): SearchHit[] {
  if (!term) return [];
  const t = term.toLowerCase();
  const out: SearchHit[] = [];
  for (const group of visibleGroups(false, NAV)) {
    for (const item of group.items) {
      if (item.label.toLowerCase().includes(t)) {
        out.push({ key: `page:${item.href}`, group: "Pages", title: item.label, detail: group.heading, href: item.href });
      }
      for (const s of item.sections ?? []) {
        if (s.label.toLowerCase().includes(t)) {
          out.push({ key: `page:${s.href}`, group: "Pages", title: s.label, detail: item.label, href: s.href });
        }
      }
    }
  }
  return out;
}

/** Results in Design 01 §4.6's groups, in GROUP_ORDER, each group in the order it came (one key once). */
export function groupHits(hits: readonly SearchHit[]): { group: SearchGroup; hits: SearchHit[] }[] {
  const seen = new Set<string>();
  const by = new Map<SearchGroup, SearchHit[]>();
  for (const h of hits) {
    if (seen.has(h.key)) continue;
    seen.add(h.key);
    by.set(h.group, [...(by.get(h.group) ?? []), h]);
  }
  return GROUP_ORDER.filter((g) => by.has(g)).map((group) => ({ group, hits: by.get(group)! }));
}

/** Today in IST as `YYYY-MM-DD`: the site's day (a meeting on or before it has a page). */
export function istToday(now: Date = new Date()): string {
  return dateInZone(now, "Asia/Kolkata");
}

// ------------------------------------------------------------------------------------------------ the answer

export type AnswerState = "answered" | "page" | "cant_answer" | "not_found" | "paused" | "limit" | "error";
export type ColumnKind = "number" | "date" | "timestamp" | "boolean" | "text";
export type AnswerColumn = { name: string; kind: ColumnKind };
export type Cell = string | boolean | null;
export type DocRef = { document: number; char_start: number; char_end: number; quote: string };
export type CitedSentence = { text: string; refs: DocRef[] };
export type CitedDocument = {
  /** core.document's id: "Open in document" (`/documents/[id]` at the span); null when the route sent none. */
  document_id: number | null;
  bank: string;
  label: string;
  title: string;
  date: string | null;
  event_slug: string | null;
  press_release: string | null;
  source_url: string | null;
};
export type PageLink = { href: string; title: string };
export type Answer = {
  route: string | null;
  state: AnswerState;
  summary: string | null;
  columns: AnswerColumn[];
  rows: Cell[][];
  sql: string | null;
  citations: CitedSentence[];
  documents: CitedDocument[];
  view: string | null;
  pages: PageLink[];
  ms: number;
};

const STATES: readonly AnswerState[] = ["answered", "page", "cant_answer", "not_found", "paused", "limit"];
const KINDS: readonly ColumnKind[] = ["number", "date", "timestamp", "boolean", "text"];
/** The pages offered when the bar can't answer and the stream gave none (the route's own list: ft.cmdbar.route). */
export const SUGGESTED_PAGES: readonly PageLink[] = [
  { href: "/policy", title: "Fed vs RBI" },
  { href: "/screener", title: "Screener" },
  { href: "/earnings", title: "Earnings calls" },
];

/** The client's own answer for a failure: the error state's words and nothing of the error. */
export function errorAnswer(): Answer {
  return { route: null, state: "error", summary: ERROR_TEXT, columns: [], rows: [], sql: null, citations: [], documents: [], view: null, pages: [], ms: 0 };
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const int = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null);

/** A page the route named: `{href, title}` (its suggestions) or an `api.search_index` row (the page found). Only site paths. */
function pageOf(v: unknown): PageLink | null {
  if (!isObj(v)) return null;
  const title = str(v.title);
  if (!title) return null;
  const href = str(v.href);
  if (href !== null) return /^\/(?!\/)[\w\-/.?=&]*$/.test(href) ? { href, title } : null;
  const item = str(v.item_type);
  const subtype = str(v.subtype);
  const slug = str(v.slug);
  if ((item !== "entity" && item !== "event") || !subtype || !slug) return null;
  const to = pageHref({ item_type: item, subtype, slug, parent_slug: str(v.parent_slug) });
  return to ? { href: to, title } : null;
}

function documentOf(v: unknown): CitedDocument | null {
  if (!isObj(v) || !str(v.title) || !str(v.bank)) return null;
  const url = str(v.source_url);
  return {
    document_id: int(v.document_id),
    bank: str(v.bank)!,
    label: str(v.label) ?? "",
    title: str(v.title)!,
    date: str(v.date),
    event_slug: str(v.event_slug),
    press_release: str(v.press_release),
    source_url: officialLink(url),
  };
}

/** An official link the box may show: https only, and never one to RBI's pages (CLAUDE.md §1; the route drops it first). */
export function officialLink(url: string | null): string | null {
  if (!url) return null;
  let host: string;
  try {
    const u = new URL(url);
    if (u.protocol !== "https:") return null;
    host = u.hostname.toLowerCase();
  } catch {
    return null;
  }
  return host === "rbi.org.in" || host.endsWith(".rbi.org.in") ? null : url;
}

/** Whether a cited document is the Fed's: only a Fed document carries "Official source ↗" (never RBI's pages: CLAUDE.md §1). */
export function isFedDocument(doc: Pick<CitedDocument, "bank">): boolean {
  return /^(fed|federal reserve)\b/i.test(doc.bank.trim());
}

/** A cited document as the box names it: bank, title, date and, for an RBI document, its press-release number (never a link). */
export function documentLine(doc: CitedDocument): string {
  return [doc.bank, doc.title, doc.date ? formatDate(doc.date) : null, doc.press_release ? `Press release ${doc.press_release}` : null]
    .filter((p): p is string => !!p)
    .join(" · ");
}

/** A cited document's page on this site (`/documents/[id]`, our stored text); null when the route sent no id. */
export function documentPage(doc: Pick<CitedDocument, "document_id">): string | null {
  return doc.document_id === null ? null : `/documents/${doc.document_id}`;
}

/** One kept sentence of a document answer, with its citation chips. */
export type CitedLine = { text: string; citations: Citation[] };

/**
 * A document answer as the box shows it (K-08, #586; Design 01 §1.10, §4.5; Spec 07 §7): each sentence with its chips, then the
 * documents those chips cite, in the order first cited.
 *
 * - **One chip per verified passage**, numbered in the order first cited across the answer; a passage cited again keeps its
 *   number. Its popover (or, on touch, its sheet) holds the route's quote (our stored text at the verified span, at most 400 code
 *   points), cut at a sentence boundary to 280 with "…" as every chip's is; the document's line (bank, title, date, an RBI
 *   press-release number); "Open in document" (`/documents/[id]` at the span); and "Official source ↗" for a Fed document only.
 * - **A sentence left with no chip isn't shown** (a reference without a quote makes no chip): no uncited AI claim (CLAUDE.md §1).
 *   The route never sends one; this holds if it ever did.
 */
export function documentAnswer(answer: Pick<Answer, "citations" | "documents">): { lines: CitedLine[]; documents: CitedDocument[] } {
  const chips = new Map<string, Citation>();
  const cited: number[] = [];
  const lines: CitedLine[] = [];
  for (const s of answer.citations) {
    if (!s.text.trim()) continue;
    const citations: Citation[] = [];
    for (const r of s.refs) {
      const doc = answer.documents[r.document];
      if (!doc || !r.quote.trim()) continue;
      const key = `${r.document}:${r.char_start}:${r.char_end}`;
      let chip = chips.get(key);
      if (!chip) {
        chip = {
          no: chips.size + 1,
          quote: cutQuote(r.quote),
          documentTitle: documentLine(doc),
          href: doc.document_id === null ? null : documentHref({ document_id: doc.document_id, char_start: r.char_start, char_end: r.char_end }),
          officialUrl: isFedDocument(doc) ? officialLink(doc.source_url) : null,
        };
        chips.set(key, chip);
        if (!cited.includes(r.document)) cited.push(r.document);
      }
      if (!citations.includes(chip)) citations.push(chip);
    }
    if (citations.length > 0) lines.push({ text: s.text.trim(), citations });
  }
  return { lines, documents: cited.map((i) => answer.documents[i]) };
}

/**
 * The route's final object, checked: every field is the type the route sends, or the whole answer is the error state. A data
 * answer's rows must each have one cell per column; a document answer's references must point into its documents.
 */
export function parseAnswer(value: unknown): Answer {
  if (!isObj(value)) return errorAnswer();
  const state = str(value.state) as AnswerState | null;
  if (!state || !STATES.includes(state)) return errorAnswer();
  const columns: AnswerColumn[] = [];
  for (const c of Array.isArray(value.columns) ? value.columns : []) {
    if (!isObj(c) || !str(c.name) || !KINDS.includes(c.kind as ColumnKind)) return errorAnswer();
    columns.push({ name: c.name as string, kind: c.kind as ColumnKind });
  }
  const rows: Cell[][] = [];
  for (const r of Array.isArray(value.rows) ? value.rows : []) {
    if (!Array.isArray(r) || r.length !== columns.length) return errorAnswer();
    if (!r.every((c) => c === null || typeof c === "string" || typeof c === "boolean")) return errorAnswer();
    rows.push(r as Cell[]);
  }
  const documents: CitedDocument[] = [];
  for (const d of Array.isArray(value.documents) ? value.documents : []) {
    const doc = documentOf(d);
    if (!doc) return errorAnswer();
    documents.push(doc);
  }
  const citations: CitedSentence[] = [];
  for (const c of Array.isArray(value.citations) ? value.citations : []) {
    if (!isObj(c) || !str(c.text) || !Array.isArray(c.refs) || c.refs.length === 0) return errorAnswer();
    const refs: DocRef[] = [];
    for (const r of c.refs) {
      if (!isObj(r)) return errorAnswer();
      const document = int(r.document);
      const start = int(r.char_start);
      const end = int(r.char_end);
      const quote = str(r.quote);
      if (document === null || document >= documents.length || start === null || end === null || end < start || quote === null) {
        return errorAnswer();
      }
      refs.push({ document, char_start: start, char_end: end, quote });
    }
    citations.push({ text: c.text as string, refs });
  }
  const pages = (Array.isArray(value.pages) ? value.pages : []).map(pageOf).filter((p): p is PageLink => p !== null);
  return {
    route: str(value.route),
    state,
    summary: str(value.summary),
    columns,
    rows,
    sql: str(value.sql),
    citations,
    documents,
    view: str(value.view),
    pages,
    ms: int(value.ms) ?? 0,
  };
}

/**
 * Reads the route's stream: each `{"status": …}` line is passed to `onStatus`; the first other line is the final object. A
 * stream that ends without one, or a line that isn't JSON, is the error state.
 */
export async function readAnswer(body: ReadableStream<Uint8Array>, onStatus: (line: string) => void): Promise<Answer> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const take = (line: string): Answer | null => {
    if (!line.trim()) return null;
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      return errorAnswer();
    }
    if (isObj(value) && typeof value.status === "string" && Object.keys(value).length === 1) {
      onStatus(value.status);
      return null;
    }
    return parseAnswer(value);
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      let nl: number;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const answer = take(buffer.slice(0, nl));
        buffer = buffer.slice(nl + 1);
        if (answer) return answer;
      }
      if (done) return take(buffer) ?? errorAnswer();
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

/** Asks one question. Never throws for the visitor: a failed request or an off-shape stream is the error state. */
export async function askQuestion(
  question: string,
  opts: { onStatus: (line: string) => void; fetch?: typeof fetch; signal?: AbortSignal },
): Promise<Answer> {
  try {
    const res = await (opts.fetch ?? fetch)("/api/cmdbar/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal: opts.signal,
    });
    if (!res.ok || !res.body) return errorAnswer();
    return await readAnswer(res.body, opts.onStatus);
  } catch (error) {
    if (opts.signal?.aborted) throw error;
    return errorAnswer();
  }
}

/** One example chip: its question and its precomputed answer (a click shows it: no model call, no limit counted). */
export type Example = { question: string; answer: Answer };

/** The checked examples (`GET /api/cmdbar/examples`), each with a usable answer; none on any failure. At most six. */
export async function fetchExamples(opts: { fetch?: typeof fetch; signal?: AbortSignal } = {}): Promise<Example[]> {
  try {
    const res = await (opts.fetch ?? fetch)("/api/cmdbar/examples", { signal: opts.signal });
    if (!res.ok) return [];
    const body = (await res.json()) as unknown;
    const list = isObj(body) && Array.isArray(body.examples) ? body.examples : [];
    const out: Example[] = [];
    for (const e of list) {
      if (!isObj(e) || !str(e.question) || !isObj(e.answer)) continue;
      const answer = parseAnswer(e.answer);
      if (answer.state === "error") continue;
      out.push({ question: e.question as string, answer });
    }
    return out.slice(0, 6);
  } catch {
    return [];
  }
}

// ------------------------------------------------------------------------------------------------ a data answer's cells

/** The plain line the route shows when no AI summary passed its check ("1 row", "N rows"): not marked as AI. */
export function plainRowCount(n: number): string {
  return n === 1 ? "1 row" : `${n} rows`;
}

/** Whether the summary is the route's AI line (marked "AI summary"), not its plain row count. */
export function isAiSummary(answer: Pick<Answer, "summary" | "rows">): boolean {
  return answer.summary !== null && answer.summary.trim() !== "" && answer.summary !== plainRowCount(answer.rows.length);
}

/**
 * A row's market, from its own `currency` (INR, USD) or `market` (IN, US) column, so a figure is grouped as its market groups
 * (Design 01 §3); undefined when the row names none.
 */
export function rowMarket(columns: readonly AnswerColumn[], row: readonly Cell[]): Market | undefined {
  for (let i = 0; i < columns.length; i++) {
    const name = columns[i].name.toLowerCase();
    const v = typeof row[i] === "string" ? (row[i] as string).toUpperCase() : null;
    if (name === "currency" && (v === "INR" || v === "USD")) return v === "INR" ? "IN" : "US";
    if (name === "market" && (v === "IN" || v === "US")) return v;
  }
  return undefined;
}

/**
 * One cell as shown: a number exactly as the row holds it, grouped by the row's market from five whole digits (so a year such
 * as 2026 is never "2,026"); a date as the tables write dates; a time as the site writes checked times; yes or no; text as is.
 */
export function formatCell(value: Cell, kind: ColumnKind, market: Market | undefined, now: string): string {
  if (value === null) return NOT_AVAILABLE;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (kind === "number") {
    const whole = /^-?(\d+)/.exec(value)?.[1] ?? "";
    return formatExactDecimal(value, whole.length >= 5 ? market : undefined);
  }
  if (kind === "date" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return formatTableDate(value);
  if (kind === "timestamp" && /(Z|[+-]\d{2}:?\d{2})$/.test(value)) return formatCheckedTime(value, now);
  return value;
}
