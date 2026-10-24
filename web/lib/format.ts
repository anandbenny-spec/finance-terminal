// web/lib/format.ts: every number, date and time on the site goes through here (Design 01 §3; CLAUDE.md §5).
// Nothing formats inline. Every example in Design 01 §3 is a unit test in format.test.ts.
//
// Rules carried from Design 01 §3 (checked on Node 22's Intl, 20 Oct 2026):
// - Month names come from a fixed table. `Intl` writes September as "Sept" in en-GB on newer ICU data and "Sep"
//   on older, so server and browser would disagree (a hydration mismatch). `Intl` is used only for digit grouping
//   and for converting time zones (formatToParts).
// - Every negative gets a true minus (U+2212), never the hyphen-minus `Intl` writes.
// - Grouping follows the figure's own market (Indian for INR and Indian indices, US for USD and US indices), never
//   the viewer's locale. Times are shown in the event's own zone; nothing reads the viewer's zone either.
//
// Input rules (so a wrong value fails loudly instead of showing a wrong figure: CLAUDE.md §1):
// - `null` or `undefined` shows "—" (not available). A NaN or infinite number throws.
// - Dates are ISO `YYYY-MM-DD` strings (Postgres `date`), parsed by hand, never through the viewer's zone.
// - Instants are ISO 8601 strings with an explicit offset (`Z` or `+05:30`), as Postgres `timestamptz` returns
//   them, or `Date` objects. A string without an offset would be read in the viewer's zone, so it throws.

export type Market = "IN" | "US";
export type Currency = "INR" | "USD";
/** The zones events are shown in: the Fed's and US markets' (shown as "ET"), the RBI's and India's ("IST"). */
export type EventZone = "America/New_York" | "Asia/Kolkata";
export type Instant = string | Date;
type Num = number | null | undefined;

export const MINUS = "−"; // true minus: Plex has it and it lines up with the plus sign
export const EN_DASH = "–"; // ranges and tallies
/** Not available: an em dash, with the tooltip `NOT_AVAILABLE_LABEL` (Design 01 §1.9, §3). */
export const NOT_AVAILABLE = "—";
export const NOT_AVAILABLE_LABEL = "Not available";
/** Doesn't apply (the SDF before 8 Apr 2022): "N/A", with a tooltip saying why (Brief §6.3). */
export const NOT_APPLICABLE = "N/A";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

const ZONE_LABEL: Record<EventZone, string> = {
  "America/New_York": "ET", // "ET" covers both EDT and EST; the IANA zone handles daylight saving
  "Asia/Kolkata": "IST",
};
const IST: EventZone = "Asia/Kolkata";

const LOCALE: Record<Market, "en-IN" | "en-US"> = { IN: "en-IN", US: "en-US" };
const CURRENCY: Record<Currency, { symbol: string; market: Market }> = {
  INR: { symbol: "₹", market: "IN" },
  USD: { symbol: "$", market: "US" },
};

// ---------------------------------------------------------------------------------------------------------------
// Numbers

type NumberStyle = { decimals: number; locale?: "en-IN" | "en-US"; grouping?: boolean; signed?: boolean };
const numberFormats = new Map<string, Intl.NumberFormat>();

/** Formats a finite number: fixed decimals, grouping by locale, a true minus; `signed` adds "+" (never to zero). */
function digits(value: number, { decimals, locale = "en-US", grouping = true, signed = false }: NumberStyle): string {
  if (!Number.isFinite(value)) throw new RangeError(`format: expected a finite number, got ${value}`);
  const key = `${locale}|${decimals}|${grouping}|${signed}`;
  let nf = numberFormats.get(key);
  if (!nf) {
    nf = new Intl.NumberFormat(locale, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
      useGrouping: grouping,
      // exceptZero: "+" and "−" but nothing for a value that rounds to zero; negative: "−" only, and never "−0.00"
      signDisplay: signed ? "exceptZero" : "negative",
    });
    numberFormats.set(key, nf);
  }
  return nf.format(value).replace(/-/g, MINUS);
}

function isMissing(value: unknown): value is null | undefined {
  return value === null || value === undefined;
}

function integer(value: number, what: string): number {
  if (!Number.isInteger(value)) throw new RangeError(`format: ${what} must be a whole number, got ${value}`);
  return value;
}

/** A percentage level with fixed decimals: policy rate `5.5` → `5.50%`; yield `4.0812` → `4.08%`. */
export function formatPercent(value: Num, decimals = 2): string {
  if (isMissing(value)) return NOT_AVAILABLE;
  return `${digits(value, { decimals, grouping: false })}%`;
}

/** Policy rate, 2 decimals always: `5.5` → `5.50%`. */
export function formatRate(value: Num): string {
  return formatPercent(value, 2);
}

/** Yield level, 2 decimals: `4.0812` → `4.08%`. */
export function formatYield(value: Num): string {
  return formatPercent(value, 2);
}

/** Fed target range: `4.00`, `4.25` → `4.00–4.25%` (en dash, one % sign). */
export function formatRateRange(lower: Num, upper: Num): string {
  if (isMissing(lower) || isMissing(upper)) return NOT_AVAILABLE;
  if (lower > upper) throw new RangeError(`formatRateRange: lower ${lower} is above upper ${upper}`);
  return `${digits(lower, { decimals: 2, grouping: false })}${EN_DASH}${digits(upper, { decimals: 2, grouping: false })}%`;
}

/**
 * A policy-rate change in basis points: `-25` → `−25 bps`, `25` → `+25 bps`, `0` → `0 bps`.
 * The decision word ("Cut", "Hike", "Hold") is a separate field and is never coloured (DecisionLine).
 */
export function formatBpsChange(bps: Num): string {
  if (isMissing(bps)) return NOT_AVAILABLE;
  return `${digits(integer(bps, "a policy-rate change in bps"), { decimals: 0, signed: true })} bps`;
}

/** A size in basis points, unsigned, for prose that carries the direction in a word: `25` → `25 bps` ("Cut 25 bps"). */
export function formatBps(bps: Num): string {
  if (isMissing(bps)) return NOT_AVAILABLE;
  const n = integer(bps, "a size in bps");
  if (n < 0) throw new RangeError(`formatBps: a size can't be negative (${n}); use formatBpsChange for a signed change`);
  return `${digits(n, { decimals: 0 })} bps`;
}

/** A value on a chart's price axis or in its table, fixed decimals, no unit (the axis names it): `4.25` → `4.25`. */
export function formatAxisNumber(value: Num, decimals = 2): string {
  if (isMissing(value)) return NOT_AVAILABLE;
  return digits(value, { decimals, grouping: false });
}

/** A range in a table column whose header carries the unit ("Fed target range (%)"): `4`, `4.25` → `4.00–4.25`. */
export function formatAxisRange(lower: Num, upper: Num): string {
  if (isMissing(lower) || isMissing(upper)) return NOT_AVAILABLE;
  return formatRateRange(lower, upper).slice(0, -1);
}

/** A signed change in a column whose header carries the unit ("Change (bps)"): `-25` → `−25`, `25` → `+25`, `0` → `0`. */
export function formatAxisChange(value: Num, decimals = 0): string {
  if (isMissing(value)) return NOT_AVAILABLE;
  return digits(value, { decimals, grouping: false, signed: true });
}

/** A yield change given in percentage points, shown in bps: `0.03` → `+3 bps` (× 100, no decimals, sign always). */
export function formatYieldChange(pp: Num): string {
  if (isMissing(pp)) return NOT_AVAILABLE;
  return `${digits(pp * 100, { decimals: 0, signed: true })} bps`;
}

/** Index level, 2 decimals, grouped by the index's market: IN `123456.7` → `1,23,456.70`; US `6812.4` → `6,812.40`. */
export function formatIndexLevel(value: Num, market: Market): string {
  if (isMissing(value)) return NOT_AVAILABLE;
  return digits(value, { decimals: 2, locale: LOCALE[market] });
}

/** Index or FX change given in percent: `-0.4213` → `−0.42%`; sign always; `0.00%` for no change. */
export function formatPctChange(pct: Num): string {
  if (isMissing(pct)) return NOT_AVAILABLE;
  return `${digits(pct, { decimals: 2, signed: true })}%`;
}

/** USD/INR (rupees per dollar), 2 decimals: `88.2731` → `88.27`. */
export function formatUsdInr(value: Num): string {
  if (isMissing(value)) return NOT_AVAILABLE;
  return digits(value, { decimals: 2, locale: LOCALE.IN });
}

/**
 * Money in its own reporting currency, grouped by that currency's market, never the viewer's:
 * INR 12345 crore → `₹12,345 cr`. Nothing converts, compares or sums across currencies (CLAUDE.md §1).
 * The units module 2 needs for USD (million, billion) are added with module 2's spec.
 */
export function formatMoney(
  amount: Num,
  currency: Currency,
  { unit, decimals = 0 }: { unit?: "crore"; decimals?: number } = {},
): string {
  if (isMissing(amount)) return NOT_AVAILABLE;
  if (unit === "crore" && currency !== "INR") throw new RangeError("formatMoney: crore is an INR unit");
  const { symbol, market } = CURRENCY[currency];
  const body = digits(Math.abs(amount), { decimals, locale: LOCALE[market] });
  const sign = amount < 0 && /[1-9]/.test(body) ? MINUS : ""; // a value that rounds to zero carries no sign
  return `${sign}${symbol}${body}${unit === "crore" ? " cr" : ""}`;
}

/** A projection as published (RBI projections carry 1 decimal): `3.5`, `3.8` → `3.5–3.8%`; one value `7` → `7.0%`. */
export function formatProjection(low: Num, high?: Num, decimals = 1): string {
  if (isMissing(low)) return NOT_AVAILABLE;
  const first = digits(low, { decimals, grouping: false });
  if (isMissing(high) || high === low) return `${first}%`;
  if (low > high) throw new RangeError(`formatProjection: low ${low} is above high ${high}`);
  return `${first}${EN_DASH}${digits(high, { decimals, grouping: false })}%`;
}

/** Vote tally: `11`, `1` → `11–1`. */
export function formatTally(majority: number, minority: number): string {
  for (const n of [majority, minority]) {
    if (integer(n, "a vote count") < 0) throw new RangeError(`formatTally: a vote count can't be negative (${n})`);
  }
  return `${majority}${EN_DASH}${minority}`;
}

// ---------------------------------------------------------------------------------------------------------------
// Dates (calendar dates: no zone involved)

type YMD = { year: number; month: number; day: number };

function parseDate(date: string): YMD {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) throw new RangeError(`format: expected a YYYY-MM-DD date, got "${date}"`);
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const check = new Date(Date.UTC(year, month - 1, day));
  if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) {
    throw new RangeError(`format: "${date}" isn't a calendar date`);
  }
  return { year, month, day };
}

function monthName(month: number): string {
  return MONTHS[month - 1];
}

/** Date in prose and headers: `2026-10-01` → `1 Oct 2026`. */
export function formatDate(date: string | null | undefined): string {
  if (isMissing(date)) return NOT_AVAILABLE;
  const { year, month, day } = parseDate(date);
  return `${day} ${monthName(month)} ${year}`;
}

/** Date in a table column, zero-padded so the column aligns: `2026-10-01` → `01 Oct 2026`. */
export function formatTableDate(date: string | null | undefined): string {
  if (isMissing(date)) return NOT_AVAILABLE;
  const { year, month, day } = parseDate(date);
  return `${String(day).padStart(2, "0")} ${monthName(month)} ${year}`;
}

/**
 * A date in a short status line (the status bar's "Next FOMC 9 Dec"): `2026-12-09` → `9 Dec` when it falls in the
 * current year in `zone`, else with its year, `2027-01-27` → `27 Jan 2027`, so a date is never read as the wrong
 * year. Browser only, after first render, like the countdown it sits beside (the server sends `formatDate`).
 */
export function formatShortDate(date: string, now: Instant, zone: EventZone = IST): string {
  const { year, month, day } = parseDate(date);
  return year === inZone(toDate(now), zone).year ? `${day} ${monthName(month)}` : `${day} ${monthName(month)} ${year}`;
}

/**
 * A time-axis tick on a chart, from a calendar date: the year (`2024`), the month (`Mar`) or the day (`5 Mar`),
 * whichever the chart's tick level asks for. Month names come from the fixed table, like every other date.
 */
export function formatAxisDate(date: string, unit: "year" | "month" | "day"): string {
  const { year, month, day } = parseDate(date);
  if (unit === "year") return String(year);
  if (unit === "month") return monthName(month);
  return `${day} ${monthName(month)}`;
}

/** Month of a monthly series: `2026-09` (or its stored date, `2026-09-01`) → `Sep 2026`. */
export function formatMonth(month: string | null | undefined): string {
  if (isMissing(month)) return NOT_AVAILABLE;
  const { year, month: m } = parseDate(/^\d{4}-\d{2}$/.test(month) ? `${month}-01` : month);
  return `${monthName(m)} ${year}`;
}

// ---------------------------------------------------------------------------------------------------------------
// Instants (shown in an event's own zone)

const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/;

function toDate(instant: Instant): Date {
  if (instant instanceof Date) {
    if (Number.isNaN(instant.getTime())) throw new RangeError("format: invalid Date");
    return instant;
  }
  if (!ISO_INSTANT.test(instant)) {
    throw new RangeError(`format: expected an ISO 8601 instant with an offset (Z or ±hh:mm), got "${instant}"`);
  }
  const d = new Date(instant);
  if (Number.isNaN(d.getTime())) throw new RangeError(`format: "${instant}" isn't a valid instant`);
  return d;
}

type ZonedParts = YMD & { hour: number; minute: number };
const zoneFormats = new Map<string, Intl.DateTimeFormat>();

/** The instant's calendar date and wall-clock time in `zone` (Intl only converts; it formats nothing we show). */
function inZone(date: Date, zone: EventZone): ZonedParts {
  let f = zoneFormats.get(zone);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      hourCycle: "h23",
      numberingSystem: "latn",
    });
    zoneFormats.set(zone, f);
  }
  const parts = f.formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((p) => p.type === type)?.value);
  return { year: part("year"), month: part("month"), day: part("day"), hour: part("hour"), minute: part("minute") };
}

function clock({ hour, minute }: ZonedParts): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function sameDay(a: YMD, b: YMD): boolean {
  return a.year === b.year && a.month === b.month && a.day === b.day;
}

/** Whole calendar days from `from` to `to` (both calendar dates). */
function daysBetween(from: YMD, to: YMD): number {
  return Math.round((Date.UTC(to.year, to.month - 1, to.day) - Date.UTC(from.year, from.month - 1, from.day)) / 86_400_000);
}

/** The instant's calendar date in `zone`, as `YYYY-MM-DD` (to compare with a stored `date`; not for display). */
export function dateInZone(instant: Instant, zone: EventZone): string {
  const { year, month, day } = inZone(toDate(instant), zone);
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/**
 * Release time in the event's own zone. RBI `2026-10-01T04:30Z` → `10:00 IST`. US events add IST in brackets:
 * Fed `2026-09-17T18:00Z` → `14:00 ET (23:30 IST)`, and the IST date when it differs: `14:00 ET (00:30 IST, 10 Dec)`.
 */
export function formatReleaseTime(instant: Instant, zone: EventZone): string {
  const date = toDate(instant);
  const local = inZone(date, zone);
  const own = `${clock(local)} ${ZONE_LABEL[zone]}`;
  if (zone === IST) return own;
  const ist = inZone(date, IST);
  const istDate = sameDay(ist, local) ? "" : `, ${ist.day} ${monthName(ist.month)}`;
  return `${own} (${clock(ist)} IST${istDate})`;
}

/**
 * When a panel's tasks last succeeded, in IST: `2026-10-20T01:11Z` → `20 Oct, 06:41 IST`.
 * The year is omitted when it's the current year (in IST), and shown otherwise: `20 Oct 2025, 06:41 IST`.
 */
export function formatCheckedTime(instant: Instant, now: Instant = new Date()): string {
  const t = inZone(toDate(instant), IST);
  const year = t.year === inZone(toDate(now), IST).year ? "" : ` ${t.year}`;
  return `${t.day} ${monthName(t.month)}${year}, ${clock(t)} IST`;
}

/**
 * Relative time, for the browser after first render only (the server sends the absolute time):
 * `3 h ago`, `2 d ago`; under an hour `12 min ago`; under a minute (or a clock running ahead) `just now`.
 */
export function formatRelativeTime(instant: Instant, now: Instant): string {
  const minutes = Math.floor((toDate(now).getTime() - toDate(instant).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

/**
 * Countdown to a scheduled event, in whole days counted in the event's own zone (as the decision-day rules in
 * `ft/jobs/due.py` do): `in 50 days`, `tomorrow`, and on the day itself `today, 14:00 ET (23:30 IST)`.
 * No ticking seconds. An event on an earlier day throws: a "next meeting" in the past is stale data.
 */
export function formatCountdown(event: Instant, zone: EventZone, now: Instant): string {
  const days = daysBetween(inZone(toDate(now), zone), inZone(toDate(event), zone));
  if (days < 0) throw new RangeError(`formatCountdown: the event (${String(event)}) was on an earlier day`);
  if (days === 0) return `today, ${formatReleaseTime(event, zone)}`;
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}
