"""us.filings: the Dow 30's statements from SEC EDGAR (Spec 02A.1 §5.2; job US-02, #401).

Module `us`, like `us.universe` (decided on #400: the hidden slice moves no count on /accuracy). Weekly, after
`us.universe`. For each US company in `config/equity/us_companies.yaml` (found in the master by its CIK), SEC's
submissions and company facts for its CIK and each predecessor CIK, through the shared client (the `sec` site: the
declared User-Agent from the SEC_USER_AGENT secret, under 10 requests a second). Every reply is stored as a raw file
(gzipped, once per content hash) and fetched whole every run (`conditional=False`), so the rows are built from the
bytes this run fetched and the task never reads the raw bucket back (Plan 05 §0.5 N10). For a multi-class issuer
(Alphabet, Nike, Visa) the latest filing's inline XBRL document is fetched too, for its cover page's shares by class.

`ft/equity/edgar.py` builds the rows (finding 14's rules; the column map is `config/equity/us_tags.yaml`). Then, in
one transaction, for each 10-K and 10-Q (and amendment) whose period ends on or after the window's start (the run's
`--since`, else us_tags.yaml's `from`) and that isn't registered yet:
- a `core.document` (doc type `results_xbrl`, source `sec`, external key the accession number, the filing's index page
  on sec.gov as its link, its raw file the company facts the rows were built from, unpublished);
- an `equity.filing` (exchange `SEC`, format `us_gaap`, basis consolidated, the period end, the fiscal year's start,
  audited for a 10-K, SEC's acceptance time when SEC's recent-filings list holds it, else none and the filing date in
  the parse note);
- its `equity.financials` rows (currency USD), with `tags` naming the element behind each value.
A filing registered by an older `edgar.PARSER_VERSION` gets its rows rebuilt from this run's bytes. A multi-class
issuer's latest filing whose shares are missing gets them once its cover page is read. A row that lacks a figure an
earlier filing's row for the same period has isn't stored (`edgar.never_displace`, #455): the filing is registered, its
parse note says which rows and why, and the earlier row stays the latest filed for that period. Nothing is published: US
companies stay unpublished until Anand's flip (Spec 02A.1 §3). A second identical run writes nothing.

Checks, B blocking, W warning:
- (B) `company_facts`: every company's submissions and company facts read, us-gaap and nothing else;
- (B) `period_end`: each filing's period end (its own durations' latest end) and form equal SEC's report date and form,
  for the filings SEC's recent list holds;
- (B) `balance_sheet`: at every registered 10-K's year end, total assets = total liabilities + temporary equity +
  total equity within 0.5% (a filer with no total-liabilities line: total assets = the filed balance sheet total);
- (B) `coverage`: each company's latest 3 fiscal years and latest 8 quarters have revenue and net income to owners,
  or us_tags.yaml names its reason;
- (B) `shares`: every share count stored is above zero;
- (W) `shares_held`: a company whose latest filing has no share count, with why (a class with no curated ratio);
- (W) `tag_switch`: a column whose figures came from more than one tag of its chain, the primary named;
- (W) `debt_held`: a company whose latest current borrowings are held (filed in parts that aren't added, no total);
- (W) `share_rates`: a multi-class issuer whose curated conversion rates took effect more than their `review_days`
  before its newest cover page (Visa's B rates fall with each litigation-escrow deposit; #455);
- (W) `kept_fuller`: the filings whose rows weren't stored because they lack figures an earlier filing's row for the
  same period has (#455);
- (W) `gaps`: for each operating company, the lines a valuation reads (GAP_WORDS) that are empty in its latest
  3 fiscal years, 8 quarters and their balance sheets, with the us-gaap tags that filing files for the period whose
  names suggest the line (tag names only, never a value): where to look before a tag joins a chain (#455);
- (W) `loaded`: what the run registered (always passing).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ft.engines.document.job import DocumentFields
from ft.equity import edgar
from ft.equity import us_universe as us
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 12
COVER_DATE = re.compile(r"cover page, as of (\d{4}-\d{2}-\d{2})")
# A valuation's lines and the words a tag that might carry each holds (the `gaps` check)
GAP_WORDS = {"revenue": ("Revenue",), "operating_income": ("OperatingIncome",), "pbt": ("BeforeIncomeTax",),
             "tax": ("IncomeTax",), "pat_owners": ("NetIncome", "ProfitLoss"), "finance_cost": ("InterestExpense",),
             "cash": ("Cash",), "total_equity": ("StockholdersEquity",), "total_assets": ("Assets",)}


def columns(tags: edgar.TagConfig) -> tuple[str, ...]:
    return tuple(tags.columns) + ("shares_outstanding",)


def companyfacts_url(cik: int) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"


def document_url(cik: int, accn: str, primary: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{primary}"


def _names(items: list[str], shown: int = SHOWN) -> str:
    return "; ".join(items[:shown]) + (f"; and {len(items) - shown} more" if len(items) > shown else "")


@dataclass(frozen=True)
class Listed:
    """A filing as SEC's submissions list it (its recent filings only)."""
    form: str
    report_date: date | None
    accepted: datetime | None
    primary: str


def parse_recent(body: bytes) -> dict[str, Listed]:
    try:
        d = json.loads(body)
    except ValueError as e:
        raise edgar.EdgarError(f"SEC's submissions reply isn't JSON ({e.__class__.__name__})") from None
    r = ((d or {}).get("filings") or {}).get("recent") or {}
    out = {}
    cols = ("accessionNumber", "form", "reportDate", "acceptanceDateTime", "primaryDocument")
    for accn, form, rep, acc, primary in zip(*(r.get(c) or [] for c in cols), strict=False):
        if not edgar.report_form(form or ""):
            continue
        accepted = None
        if acc:
            try:
                accepted = datetime.fromisoformat(acc)
            except ValueError:
                accepted = None
        out[accn] = Listed(form, date.fromisoformat(rep) if rep else None, accepted, primary or "")
    return out


@dataclass
class Company:
    c: us.UsCompany
    entity_id: int | None
    raw: dict[int, int] = field(default_factory=dict)                 # CIK -> the company facts' raw file
    listed: dict[str, Listed] = field(default_factory=dict)
    stmts: edgar.Statements | None = None
    built: list[edgar.Built] = field(default_factory=list)            # every filing in the window
    new: list[str] = field(default_factory=list)                      # accession numbers to register
    rebuild: list[str] = field(default_factory=list)                  # registered by an older parser version
    fill_shares: tuple[str, int, tuple] | None = None                 # (accn, document id, (value, how))
    error: str | None = None


@dataclass
class Fetched:
    start: date
    tags: edgar.TagConfig
    companies: list[Company] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        return [f"{x.c.ticker} ({x.error})" for x in self.companies if x.error]


@register
class UsFilings(Task):
    key = "us.filings"
    version = edgar.PARSER_VERSION

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        if not db.execute("select exists (select 1 from core.source where key = 'sec')").fetchone()[0]:
            return Due(False, "not set up: migration 0039 (us_market) isn't pushed to this project")
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.financials")

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        cfg, tags = us.load_us_config(), edgar.load_tags()
        ua = us.sec_user_agent()                     # fails at once, naming the secret, when it's empty
        f = ctx.fetcher
        out = Fetched(ctx.since or tags.start, tags)
        ids = dict(f.db.execute(
            """select i.value, i.entity_id from core.entity_identifier i join core.entity e on e.id = i.entity_id
                where e.kind = 'company' and e.market = 'US' and i.scheme = 'cik' and upper_inf(i.valid)""").fetchall())
        stored = {r[0]: (r[1], r[2]) for r in f.db.execute(
            """select d.external_key, fl.document_id, fl.parser_version from equity.filing fl
                 join core.document d on d.id = fl.document_id where fl.exchange = 'SEC'""").fetchall()}
        for c in cfg.companies:
            x = Company(c, ids.get(str(c.cik)))
            out.companies.append(x)
            if x.entity_id is None:
                x.error = "not in the company master: us.universe runs first"
                continue
            try:
                self._company(f, ua, x, tags, out.start, stored)
            except (FetchError, edgar.EdgarError, ValueError) as e:
                x.error = str(e)
        return out

    def _get(self, f, url: str, ua: str, media_type: str, ext: str):
        return f.get(url, source="sec", headers={"User-Agent": ua}, media_type=media_type, ext=ext, conditional=False)

    def _company(self, f, ua: str, x: Company, tags: edgar.TagConfig, start: date, stored: dict) -> None:
        c = x.c
        x.listed = parse_recent(self._get(f, us.submissions_url(c.cik), ua, "application/json", "json").body)
        docs = []
        for cik in (c.cik, *c.predecessor_ciks):
            got = self._get(f, companyfacts_url(cik), ua, "application/json", "json")
            try:
                cf = json.loads(got.body)
            except ValueError as e:
                raise edgar.EdgarError(f"company facts for CIK {cik} aren't JSON ({e.__class__.__name__})") from None
            if "ifrs-full" in (cf.get("facts") or {}):
                raise edgar.EdgarError(f"CIK {cik} files IFRS facts (the slice reads US GAAP filers only)")
            x.raw[cik] = got.raw_file_id
            docs.append((cik, cf))
        x.stmts = s = edgar.Statements(docs, tags, c.ticker)
        window = sorted((f_ for f_ in s.filings.values() if (pe := f_.period_end) is not None and pe >= start),
                        key=lambda f_: (f_.filed, f_.accn))
        latest = window[-1] if window else None

        cover = None
        held = None
        if c.multi_class:
            held = (None, ("held: a multi-class issuer's shares by class aren't in company facts; only the latest "
                           "filing's cover page is read"))
            need = latest is not None and (latest.accn not in stored or self._no_shares(f, stored[latest.accn][0]))
            if need:
                cover = self._cover(f, ua, x, latest, tags)

        for fl in window:
            share = (cover if fl is latest and cover is not None else held) if c.multi_class else None
            b = s.build(fl, cover=share)
            if b is not None:
                x.built.append(b)
        edgar.never_displace(x.built)
        for b in x.built:
            fl = b.filing
            if fl.accn not in stored:
                if b.rows or b.dropped:
                    x.new.append(fl.accn)
            elif str(stored[fl.accn][1]) != str(self.version):
                x.rebuild.append(fl.accn)
        last = next((b for b in x.built if b.filing is latest), None)
        if c.multi_class and latest is not None and latest.accn in stored and cover is not None \
                and latest.accn not in x.rebuild and not (last is not None and "instant" in last.dropped):
            x.fill_shares = (latest.accn, stored[latest.accn][0], cover)

    @staticmethod
    def _no_shares(f, document_id: int) -> bool:
        return not f.db.execute("""select exists (select 1 from equity.financials where document_id = %s
                                                   and period_kind = 'instant' and shares_outstanding is not null)""",
                                (document_id,)).fetchone()[0]

    def _cover(self, f, ua: str, x: Company, latest: edgar.Filing, tags: edgar.TagConfig) -> tuple:
        listed = x.listed.get(latest.accn)
        if listed is None or not listed.primary:
            return None, "held: the latest filing isn't in SEC's recent list, so its cover page wasn't read"
        got = self._get(f, document_url(latest.cik, latest.accn, listed.primary), ua, "text/html", "htm")
        try:
            cov = edgar.read_cover(got.body)
        except edgar.EdgarError as e:
            return None, f"held: the cover page couldn't be read ({e})"
        return edgar.cover_total(cov, tags.share_classes.get(x.c.ticker))

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        """Batched per company (dev run 1 spent 19 minutes on one round trip per row): the new filings' documents, their
        `equity.filing` rows and their statement rows, each in one `executemany`."""
        sec = tx.execute("select id from core.source where key = 'sec'").fetchone()[0]
        COLUMNS = columns(f.tags)
        insert = (f"""insert into equity.financials (document_id, entity_id, basis, period_start, period_end,
                                                     period_kind, currency, {", ".join(COLUMNS)}, tags)
                      values (%s, %s, 'consolidated', %s, %s, %s, 'USD', {", ".join(["%s"] * len(COLUMNS))}, %s)""")
        n, registered, rebuilt, rows = 0, 0, 0, {}
        for x in f.companies:
            if x.error or x.stmts is None:
                continue
            by = {b.filing.accn: b for b in x.built}
            doc_ids: dict[str, int] = {}
            new = [by[a] for a in x.new]
            if new:
                doc_ids = dict(zip(x.new, self._documents(tx, [
                    DocumentFields("results_xbrl", x.entity_id, sec, b.filing.accn, b.filing.link(),
                                   f"{x.c.name} {b.filing.form}, period ended {b.period_end.isoformat()}",
                                   x.raw[b.filing.cik], self._accepted(x, b.filing.accn)) for b in new]), strict=True))
                with tx.cursor() as cur:
                    cur.executemany(
                        """insert into equity.filing (document_id, entity_id, basis, format, period_end, fy_start, fy_end,
                                                      audited, filed_at, source_url, exchange, parse_status, parse_note,
                                                      parser_version)
                           values (%s, %s, 'consolidated', 'us_gaap', %s, %s, %s, %s, %s, %s, 'SEC', 'parsed', %s, %s)""",
                        [(doc_ids[b.filing.accn], x.entity_id, b.period_end, b.fy_start,
                          b.period_end if b.filing.annual else None, None if b.filing.amendment else b.filing.annual,
                          self._accepted(x, b.filing.accn), b.filing.link(), self._note(x, b), str(self.version))
                         for b in new])
                registered += len(new)
            for accn in x.rebuild:
                b = by[accn]
                doc_id = doc_ids[accn] = tx.execute(
                    "select id from core.document where source_id = %s and external_key = %s order by version_no desc "
                    "limit 1", (sec, accn)).fetchone()[0]
                kept = tx.execute("""select shares_outstanding, tags->>'shares_outstanding' from equity.financials
                                      where document_id = %s and period_kind = 'instant'""", (doc_id,)).fetchone()
                tx.execute("delete from equity.financials where document_id = %s", (doc_id,))
                tx.execute("""update equity.filing set parse_status = %s, parse_note = %s, parser_version = %s
                               where document_id = %s""",
                           ("parsed" if b.rows or b.dropped else "failed", self._note(x, b), str(self.version), doc_id))
                if x.c.multi_class and kept and kept[0] is not None:
                    for r in b.rows:
                        if r.period_kind == "instant" and r.values.get("shares_outstanding") is None:
                            r.values["shares_outstanding"], r.tags["shares_outstanding"] = kept[0], kept[1]
                rebuilt += 1
            params = [(doc_ids[accn], x.entity_id, r.period_start, r.period_end, r.period_kind,
                       *[r.values.get(col) for col in COLUMNS], json.dumps(r.tags, ensure_ascii=False))
                      for accn in x.new + x.rebuild for r in by[accn].rows]
            if params:
                with tx.cursor() as cur:
                    cur.executemany(insert, params)
                for p in params:
                    rows[p[4]] = rows.get(p[4], 0) + 1
                n += len(params)
            if x.fill_shares is not None and x.fill_shares[2][0] is not None:
                _accn, doc_id, (value, how) = x.fill_shares
                end = tx.execute("select period_end from equity.filing where document_id = %s", (doc_id,)).fetchone()[0]
                got = tx.execute("""update equity.financials set shares_outstanding = %s,
                                           tags = coalesce(tags, '{}'::jsonb) || jsonb_build_object('shares_outstanding', %s::text)
                                     where document_id = %s and period_kind = 'instant' and shares_outstanding is null""",
                                 (value, how, doc_id)).rowcount
                if not got and not tx.execute("select exists (select 1 from equity.financials where document_id = %s "
                                              "and period_kind = 'instant')", (doc_id,)).fetchone()[0]:
                    tx.execute("""insert into equity.financials (document_id, entity_id, basis, period_end, period_kind,
                                                                 currency, shares_outstanding, tags)
                                  values (%s, %s, 'consolidated', %s, 'instant', 'USD', %s, %s)""",
                               (doc_id, x.entity_id, end, value, json.dumps({"shares_outstanding": how})))
                    got = 1
                n += got
        self.result = {"registered": registered, "rebuilt": rebuilt, "rows": rows}
        return n

    @staticmethod
    def _accepted(x: Company, accn: str) -> datetime | None:
        listed = x.listed.get(accn)
        return listed.accepted if listed else None

    def _note(self, x: Company, b: edgar.Built) -> str:
        fl = b.filing
        return (f"v{self.version}: {fl.form} filed {fl.filed.isoformat()}"
                + ("" if self._accepted(x, fl.accn) else " (its acceptance time isn't in SEC's recent filings list)")
                + f"; {len(b.rows)} rows from company facts (CIK {fl.cik})"
                + (f"; {'; '.join(b.notes)}" if b.notes else ""))

    @staticmethod
    def _documents(tx, docs: list[DocumentFields]) -> list[int]:
        """New version-1 `core.document` rows in one round trip, unpublished and without text: `insert_document`'s
        columns for a document seen for the first time (its `previous` is None)."""
        with tx.cursor() as cur:
            cur.executemany(
                """insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url,
                                              raw_file_id, published_at, version_no, supersedes_id, language)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, null, %s) returning id""",
                [(d.doc_type, d.entity_id, d.event_id, d.title, d.source_id, d.external_key, d.source_url, d.raw_file_id,
                  d.published_at, d.language) for d in docs], returning=True)
            ids = []
            while True:
                ids.append(cur.fetchone()[0])
                if not cur.nextset():
                    break
        return ids

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        out = []
        ok = [x for x in f.companies if not x.error]
        out.append(Check("company_facts", not f.failed,
                         ("couldn't be read: " + _names(f.failed)) if f.failed
                         else f"{len(ok)} of {len(f.companies)} companies' submissions and company facts read (US GAAP)"))

        bad, checked, unlisted = [], 0, 0
        for x in ok:
            for b in x.built:
                listed = x.listed.get(b.filing.accn)
                if listed is None:
                    unlisted += 1
                    continue
                checked += 1
                if listed.report_date != b.period_end or listed.form != b.filing.form:
                    bad.append(f"{x.c.ticker} {b.filing.accn}: {b.filing.form} ended {b.period_end} here, "
                               f"{listed.form} ended {listed.report_date} at SEC")
        out.append(Check("period_end", not bad, ("differs from SEC: " + _names(bad)) if bad else
                         f"{checked} filings' period ends and forms equal SEC's ({unlisted} not in SEC's recent list)"))

        bad, n10k, partial, skipped, twice = [], 0, [], [], []
        for x in ok:
            for b in x.built:
                if b.balance is None:
                    continue
                tied, how = b.balance
                if tied is None:
                    skipped.append(f"{x.c.ticker} {b.period_end}: {how}")
                    continue
                n10k += 1
                if not tied:
                    bad.append(f"{x.c.ticker} {b.period_end}: {how}")
                elif "no total-liabilities line" in how:
                    partial.append(x.c.ticker)
                if tied and "values at the date" in how:
                    twice.append(f"{x.c.ticker} {b.period_end}: {how}")
        out.append(Check("balance_sheet", not bad, ("doesn't tie within 0.5%: " + _names(bad, 40)) if bad else
                         f"{n10k} year ends tie within 0.5%"
                         + (f" ({', '.join(sorted(set(partial)))}: no total-liabilities line, so against the balance "
                            "sheet total)" if partial else "")
                         + (f"; {_names(twice)}" if twice else "")
                         + (f"; not checked: {_names(skipped)}" if skipped else "")))

        out.append(self._coverage(tx, f, ok))

        stored = [(x.c.ticker, b.period_end, r.values["shares_outstanding"]) for x in ok for b in x.built
                  if b.filing.accn in x.new or b.filing.accn in x.rebuild
                  for r in b.rows if r.values.get("shares_outstanding") is not None]
        stored += [(x.c.ticker, "latest", x.fill_shares[2][0]) for x in ok
                   if x.fill_shares is not None and x.fill_shares[2][0] is not None]
        bad = [f"{t} {e}: {v}" for t, e, v in stored if v <= 0]
        out.append(Check("shares", not bad, ("not above zero: " + _names(bad)) if bad
                         else f"{len(stored)} share counts, every one above zero"))

        held = []
        for x in ok:
            row = tx.execute(
                """select fn.tags->>'shares_outstanding' from equity.financials fn
                     join equity.filing fl on fl.document_id = fn.document_id
                    where fl.entity_id = %s and fl.exchange = 'SEC' and fn.period_kind = 'instant'
                      and fn.shares_outstanding is null
                      and fl.period_end = (select max(period_end) from equity.filing where entity_id = %s and exchange = 'SEC')
                    limit 1""", (x.entity_id, x.entity_id)).fetchone()
            if row is not None:
                held.append(f"{x.c.ticker}: {row[0] or 'no cover-page share count'}")
        out.append(Check("shares_held", not held, _names(held) if held
                         else "every company's latest filing has its share count", blocking=False))

        switched = []
        for x in ok:
            used: dict[str, set[str]] = {}
            for b in x.built:
                for r in b.rows:
                    for col, how in r.tags.items():
                        if col != "shares_outstanding":
                            used.setdefault(col, set()).add(how)
            switched += [f"{x.c.ticker} {s}" for s in x.stmts.switched(used)]
        out.append(Check("tag_switch", not switched, _names(switched) if switched
                         else "every column's figures came from one tag", blocking=False))

        debt = []
        for x in ok:
            if not x.built:
                continue
            last = max(x.built, key=lambda b: (b.period_end, b.filing.filed))
            how = next((r.tags.get("borrowings_current") for r in last.rows if r.period_kind == "instant"), None)
            if how and how.startswith("held"):
                debt.append(f"{x.c.ticker} {last.period_end}: {how[len('held: '):]}")
        out.append(Check("debt_held", not debt, ("current borrowings held: " + _names(debt)) if debt
                         else "every company's latest current borrowings are one filed line or the sum of its filed "
                              "captions", blocking=False))

        out.append(self._share_rates(tx, f, ok))

        kept = [f"{x.c.ticker} {b.filing.form} {b.filing.accn} ({', '.join(b.dropped)})" for x in ok for b in x.built
                if b.dropped]
        out.append(Check("kept_fuller", not kept, ("rows not stored, as they lack figures an earlier filing's row for the "
                                                   "same period has: " + _names(kept)) if kept
                         else "no filing's row lacks a figure an earlier filing's row for its period has", blocking=False))

        out.append(self._gaps(ok))

        r = self.result
        out.append(Check("loaded", True, f"{r.get('registered', 0)} filings registered, {r.get('rebuilt', 0)} rebuilt; "
                                         "rows by period kind: " + (", ".join(f"{k} {v}" for k, v in
                                                                              sorted(r.get("rows", {}).items())) or "none")
                         + f"; window from {f.start.isoformat()}", blocking=False))
        return out

    @staticmethod
    def _share_rates(tx, f: Fetched, ok: list[Company]) -> Check:
        """A multi-class issuer's rates that took effect more than `review_days` before its newest cover page (read from
        the latest filing's stored share count, which names the cover page's date)."""
        due = []
        for x in ok:
            classes = f.tags.share_classes.get(x.c.ticker)
            if classes is None or not classes.review_days:
                continue
            row = tx.execute(
                """select fn.tags->>'shares_outstanding' from equity.financials fn
                     join equity.filing fl on fl.document_id = fn.document_id
                    where fl.entity_id = %s and fl.exchange = 'SEC' and fn.period_kind = 'instant'
                      and fn.shares_outstanding is not null
                    order by fn.period_end desc, fn.document_id desc limit 1""", (x.entity_id,)).fetchone()
            m = COVER_DATE.search(row[0] or "") if row else None
            if m is None:
                continue
            end = date.fromisoformat(m.group(1))
            stale = classes.stale(end)
            if stale:
                since = sorted({classes.rates[k].effective.isoformat() for k in stale})
                due.append(f"{x.c.ticker}: {', '.join(stale)} took effect {', '.join(since)}, more than "
                           f"{classes.review_days} days before its cover page of {end.isoformat()}: update them in "
                           f"us_tags.yaml share_classes.{x.c.ticker} from its latest 8-K or 10-K")
        return Check("share_rates", not due, _names(due) if due else
                     "every curated conversion rate took effect within its review days of the newest cover page",
                     blocking=False)

    @staticmethod
    def _gaps(ok: list[Company]) -> Check:
        """Each operating company's empty valuation lines in its latest 3 fiscal years, 8 quarters and their balance
        sheets (the latest stored row per period), grouped by line, with the us-gaap tags filed for those periods whose
        names suggest the line. Tag names and dates only."""
        found = []
        for x in ok:
            if x.c.valuation_family != "operating" or x.stmts is None:
                continue
            latest: dict[tuple, tuple] = {}
            for b in sorted(x.built, key=lambda b: (b.filing.filed, b.filing.accn)):
                for r in b.rows:
                    latest[(r.period_kind, r.period_end)] = (r, b)
            years = sorted({b.period_end for b in x.built if b.filing.annual})[-3:]
            quarters = sorted({b.period_end for b in x.built})[-8:]
            want = [("year", e) for e in years] + [("quarter", e) for e in quarters] + \
                   [("instant", e) for e in sorted(set(years) | set(quarters[-1:]))]
            per: dict[str, tuple[list[str], set[str]]] = {}
            for kind, end in want:
                got = latest.get((kind, end))
                for col, words in GAP_WORDS.items():
                    instant = x.stmts.cfg.columns[col].kind == "instant"
                    if instant != (kind == "instant"):
                        continue
                    if got is not None and got[0].values.get(col) is not None:
                        continue
                    label, tags = per.setdefault(col, ([], set()))
                    label.append(f"{kind} {end.isoformat()}" + ("" if got is None else
                                 f" ({got[0].tags[col]})" if str(got[0].tags.get(col) or "").startswith("held") else ""))
                    if got is not None:
                        r, b = got
                        starts = {None} if instant else {r.period_start, b.fy_start}
                        for st in starts:
                            tags.update(x.stmts.candidates(b.filing, col, st, end, words))
            for col, (label, tags) in per.items():
                found.append(f"{x.c.ticker} {col} empty in {len(label)} of its periods ({_names(label, 4)}); filed for "
                             f"them: {', '.join(sorted(tags)) or 'no tag holding ' + ' or '.join(GAP_WORDS[col])}")
        return Check("gaps", not found, _names(found, 40) if found else
                     "every operating company's valuation lines are filed for its latest years and quarters",
                     blocking=False)

    @staticmethod
    def _why(x: Company, kind: str, end) -> str:
        """Why a period has no revenue or net income: the column's tag (held, and why) or that none is filed."""
        r = next((r for b in reversed(x.built) for r in b.rows if r.period_kind == kind and r.period_end == end), None)
        if r is None:
            return " (no row: the filing has no such period)"
        return " (" + ", ".join(f"{c}: {r.tags.get(c) or 'no tag of its chain is filed'}"
                                for c in ("revenue", "pat_owners") if r.values.get(c) is None) + ")"

    @staticmethod
    def _coverage(tx, f: Fetched, ok: list[Company]) -> Check:
        """Each company's latest 3 fiscal years (its 10-Ks' period ends) and latest 8 quarters (its filings' period
        ends) have a row with revenue and net income to owners."""
        bad, excused = [], []
        for x in ok:
            reason = f.tags.coverage_exceptions.get(x.c.ticker)
            have = {(k, e) for k, e in tx.execute(
                """select fn.period_kind, fn.period_end from equity.financials fn
                     join equity.filing fl on fl.document_id = fn.document_id
                    where fl.entity_id = %s and fl.exchange = 'SEC' and fn.period_kind in ('year', 'quarter')
                      and fn.revenue is not null and fn.pat_owners is not null""", (x.entity_id,)).fetchall()}
            years = [e for (e,) in tx.execute(
                """select distinct period_end from equity.filing where entity_id = %s and exchange = 'SEC'
                      and fy_end is not null order by 1 desc limit 3""", (x.entity_id,)).fetchall()]
            quarters = [e for (e,) in tx.execute(
                """select distinct period_end from equity.filing where entity_id = %s and exchange = 'SEC'
                    order by 1 desc limit 8""", (x.entity_id,)).fetchall()]
            gaps = [f"FY {e}{UsFilings._why(x, 'year', e)}" for e in years if ("year", e) not in have] + \
                   [f"Q {e}{UsFilings._why(x, 'quarter', e)}" for e in quarters if ("quarter", e) not in have]
            short = (["fewer than 3 fiscal years"] if len(years) < 3 else []) + \
                    (["fewer than 8 quarters"] if len(quarters) < 8 else [])
            if gaps or short:
                (excused if reason else bad).append(f"{x.c.ticker}: " + ", ".join(short + gaps)
                                                    + (f" ({reason})" if reason else ""))
        return Check("coverage", not bad, ("missing revenue or net income: " + _names(bad, 40)) if bad else
                     f"{len(ok)} companies have 3 fiscal years and 8 quarters of revenue and net income"
                     + (f"; named reasons: {_names(excused)}" if excused else ""))
