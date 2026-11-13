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

/** A share of a text in percent with its sign always, one decimal: `12.34` → `+12.3%`, `-4` → `−4.0%`, `0` → `0.0%`. */
export function formatShareChange(pct: Num, decimals = 1): string {
  if (isMissing(pct)) return NOT_AVAILABLE;
  return `${digits(pct, { decimals, grouping: false, signed: true })}%`;
}

/** A share of a text in percent, no sign, one decimal: `12.34` → `12.3%`. */
export function formatShare(pct: Num, decimals = 1): string {
  if (isMissing(pct)) return NOT_AVAILABLE;
  return `${digits(pct, { decimals, grouping: false })}%`;
}

/** A chart-axis label in percent, signed, whole numbers: `60` → `+60%`, `-60` → `−60%`, `0` → `0%`. */
export function formatAxisShare(pct: Num): string {
  return formatShareChange(pct, 0);
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
 * INR 12345 crore → `₹12,345 cr`; USD 12345 million → `$12,345 mn`. Nothing converts, compares or sums across
 * currencies (CLAUDE.md §1). Each unit belongs to one currency: crore to INR, million to USD (US-06, #405).
 */
export function formatMoney(
  amount: Num,
  currency: Currency,
  { unit, decimals = 0 }: { unit?: "crore" | "million"; decimals?: number } = {},
): string {
  if (isMissing(amount)) return NOT_AVAILABLE;
  if (unit === "crore" && currency !== "INR") throw new RangeError("formatMoney: crore is an INR unit");
  if (unit === "million" && currency !== "USD") throw new RangeError("formatMoney: million is a USD unit here");
  const { symbol, market } = CURRENCY[currency];
  const body = digits(Math.abs(amount), { decimals, locale: LOCALE[market] });
  const sign = amount < 0 && /[1-9]/.test(body) ? MINUS : ""; // a value that rounds to zero carries no sign
  return `${sign}${symbol}${body}${unit === "crore" ? " cr" : unit === "million" ? " mn" : ""}`;
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
 * The calendar day a filing was made, as `YYYY-MM-DD`, for `formatDate`. An Indian filing's day is the day in IST: an exchange
 * list date is stored as that day's midnight IST (18:30 UTC the day before), and a results filing can be broadcast between
 * midnight and 05:30 IST, so the instant's UTC date would be a day early (found by #191's page check, 12 Nov 2026). A US filing
 * keeps the instant's UTC date, as before: which day an evening acceptance counts on is an open question (#448).
 */
export function filingDay(instant: string | null | undefined, market: Market): string | null {
  if (!instant) return null;
  return market === "IN" ? dateInZone(instant, IST) : instant.slice(0, 10);
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

// ---------------------------------------------------------------------------------------------------------------
// Admin (C6; Design 01 §4.7): the refresh button's running time and cooldown

/** A run's elapsed time, for the admin's refresh button: `72` s → `1:12`; from an hour, `1:02:05`. Whole seconds. */
export function formatElapsed(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) throw new RangeError(`formatElapsed: expected seconds ≥ 0, got ${seconds}`);
  const s = Math.floor(seconds);
  const pad = (n: number) => String(n).padStart(2, "0");
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

/**
 * Whole minutes until an instant, rounded up so a cooldown never reads as over before it is: `32 min`; under a
 * minute, `1 min`. Null once the instant has passed (it's available now).
 */
export function formatMinutesUntil(instant: Instant, now: Instant): string | null {
  const ms = toDate(instant).getTime() - toDate(now).getTime();
  if (ms <= 0) return null;
  return `${Math.ceil(ms / 60_000)} min`;
}

/** A whole count (rows written, open alerts): `1234` → `1,234`. Never the viewer's locale. A count of a company's own
 * (its shares) passes its market and is grouped as that market groups: `1234567` → `12,34,567` for IN. */
export function formatCount(value: number, market?: Market): string {
  return digits(integer(value, "a count"), { decimals: 0, locale: market ? LOCALE[market] : undefined });
}

// ---------------------------------------------------------------------------------------------------------------
// Company figures (module 2; Spec 02A §8). Ratios are stored as fractions (0.18 is 18%); the rupee amounts in full rupees.

/** A stored fraction as a percentage level, one decimal by default: `0.1834` → `18.3%`, `-0.05` → `−5.0%`. */
export function formatFraction(fraction: Num, decimals = 1): string {
  if (isMissing(fraction)) return NOT_AVAILABLE;
  return `${digits(fraction * 100, { decimals, grouping: false })}%`;
}

/** A stored fraction as a signed percentage, for a market move (1-year return): `0.2` → `+20.0%`, `-0.0412` → `−4.1%`. */
export function formatFractionChange(fraction: Num, decimals = 1): string {
  if (isMissing(fraction)) return NOT_AVAILABLE;
  return `${digits(fraction * 100, { decimals, grouping: false, signed: true })}%`;
}

/** A stored fraction of a percentage-point gap as points, signed: `0.04` → `+4.0 pts`. */
export function formatFractionPoints(fraction: Num, decimals = 1): string {
  if (isMissing(fraction)) return NOT_AVAILABLE;
  return `${digits(fraction * 100, { decimals, grouping: false, signed: true })} pts`;
}

/** A multiple (P/E, P/B, EV/EBIT, debt to equity): `23.456` → `23.5×`; grouped by the figure's market. */
export function formatMultiple(value: Num, market: Market = "IN", decimals = 1): string {
  if (isMissing(value)) return NOT_AVAILABLE;
  return `${digits(value, { decimals, locale: LOCALE[market] })}×`;
}

/** A per-share price in its own currency, 2 decimals: INR `1234.5` → `₹1,234.50`, USD `98.1` → `$98.10`. */
export function formatPrice(value: Num, currency: Currency): string {
  return formatMoney(value, currency, { decimals: 2 });
}

/** Rupees given in full, shown in crore (1 crore = 10,000,000): `1.2345e12` → `₹1,23,450 cr`. */
export function formatRupeesAsCrore(rupees: Num): string {
  if (isMissing(rupees)) return NOT_AVAILABLE;
  return formatMoney(rupees / 10_000_000, "INR", { unit: "crore" });
}

/**
 * Rupees given in full, shown in crore for a list of related-party values (VC-06f, #518): from ₹1 cr up exactly as
 * `formatRupeesAsCrore`; under ₹1 cr two decimals, so a non-zero amount never reads as `₹0 cr` or rounds up to `₹1 cr`;
 * below ₹0.005 cr (two decimals would print 0.00) `less than ₹0.01 cr`. `4_300_000` → `₹0.43 cr`.
 */
export function formatRupeesAsCroreFine(rupees: Num): string {
  if (isMissing(rupees)) return NOT_AVAILABLE;
  const crore = rupees / 10_000_000;
  const abs = Math.abs(crore);
  if (abs >= 1 || abs === 0) return formatRupeesAsCrore(rupees);
  if (abs < 0.005) return `${crore < 0 ? MINUS : ""}less than ₹0.01 cr`;
  return formatMoney(crore, "INR", { unit: "crore", decimals: 2 });
}

/** Dollars given in full, shown in millions with US grouping (US-06, #405; the US twin of crore): `3.94328e11` → `$394,328 mn`. */
export function formatDollarsAsMillion(dollars: Num): string {
  if (isMissing(dollars)) return NOT_AVAILABLE;
  return formatMoney(dollars / 1_000_000, "USD", { unit: "million" });
}

/** Money given in full units of its currency, in that currency's display unit: crore for rupees, millions for dollars. */
export function formatAmount(value: Num, currency: Currency): string {
  return currency === "INR" ? formatRupeesAsCrore(value) : formatDollarsAsMillion(value);
}

/** A percentage level that may exceed 100 and is grouped by the figure's market, for a value shown as filed (the promoter note, #116): `6613` → `6,613.00%`, `66.13` → `66.13%`. */
export function formatPercentLevel(pct: Num, market: Market = "IN", decimals = 2): string {
  if (isMissing(pct)) return NOT_AVAILABLE;
  return `${digits(pct, { decimals, locale: LOCALE[market] })}%`;
}

/** The PDF page(s) of a cited passage: `7, 7` → `p. 7`, `7, 8` → `pp. 7–8`. */
export function formatPageRange(first: number, last: number | null | undefined = first): string {
  const a = digits(integer(first, "a page"), { decimals: 0, grouping: false });
  if (isMissing(last) || last === first) return `p. ${a}`;
  return `pp. ${a}${EN_DASH}${digits(integer(last, "a page"), { decimals: 0, grouping: false })}`;
}

/**
 * A figure management stated on an earnings call, exactly as the quote writes it (no rounding, no added decimals), Indian
 * grouping: 12.5 with unit pct → 12.5%; 4500 with unit inr → ₹4,500 cr (stored in crore); 3 with unit other → 3 (unit as spoken).
 * The value is the AI-extracted figure (Spec 02B §13.6), so its decimals are the ones it carries, at most four.
 */
export function formatStatedValue(value: Num, unit: string | null | undefined): string {
  if (isMissing(value)) return NOT_AVAILABLE;
  const decimals = Math.min(4, (String(Math.abs(value)).split(".")[1] ?? "").length);
  const body = digits(value, { decimals, locale: "en-IN" });
  if (unit === "pct") return `${body}%`;
  if (unit === "inr") return `${value < 0 ? MINUS : ""}₹${digits(Math.abs(value), { decimals, locale: "en-IN" })} cr`;
  return `${body} (unit as spoken)`;
}

// ---------------------------------------------------------------------------------------------------------------
// The value chain card (VC-06, #191; Spec 02B §12.4, §12.7). A BRSR share arrives in percent, exact; a change is said in words.

/**
 * A BRSR share exactly as filed, in percent, with the decimals it carries (at most four): `90.4` → `90.4%`, `0.07` → `0.07%`,
 * `96` → `96%`. A hand check compares a share "to its filed decimals" (§12.8), so nothing is rounded or padded.
 */
export function formatFiledShare(pct: Num): string {
  if (isMissing(pct)) return NOT_AVAILABLE;
  const decimals = Math.min(4, (String(Math.abs(pct)).split(".")[1] ?? "").length);
  return `${digits(pct, { decimals, grouping: false })}%`;
}

/**
 * A change in a share between two years, in words with no sign and no colour: `3.24` → `up 3.2 pp`, `-1` → `down 1.0 pp`,
 * `0` → `unchanged`. A change too small for one decimal keeps the decimals it needs (`-0.04` → `down 0.04 pp`, at most four,
 * the most a filed share carries): the two years' figures are shown as filed beside it, so "unchanged" is said only of a
 * change that is nothing (#191's page check, 12 Nov 2026: 1.44% to 1.4% read "unchanged").
 */
export function formatShareChangeWords(pp: Num): string {
  if (isMissing(pp)) return NOT_AVAILABLE;
  for (let decimals = 1; decimals <= 4; decimals++) {
    const body = digits(Math.abs(pp), { decimals, grouping: false });
    if (/[1-9]/.test(body)) return `${pp > 0 ? "up" : "down"} ${body} pp`;
  }
  return "unchanged";
}

/** A change in a count between two years, in words: `120` → `up 120`, `-3` → `down 3`, `0` → `unchanged`. Grouped as India groups. */
export function formatCountChangeWords(change: number | null | undefined): string {
  if (isMissing(change)) return NOT_AVAILABLE;
  if (integer(change, "a count change") === 0) return "unchanged";
  return `${change > 0 ? "up" : "down"} ${digits(Math.abs(change), { decimals: 0, locale: "en-IN" })}`;
}
