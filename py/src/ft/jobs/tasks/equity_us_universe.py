"""us.universe: the Dow 30's company master, Dow membership and US peer groups (Spec 02A.1 §5.1; job US-01, #400).

The US tasks are module `us` (`us.*`), never `equity.*`: `site.quality_summary` counts only the modules in
`config/quality.yaml`, so the hidden slice moves no count on /accuracy (decided on #400, 11 Nov 2026; US-09 adds `us`
after the flip).

Weekly. From the curated `config/equity/us_companies.yaml` and `us_peer_groups.yaml` (`ft.equity.us_universe`), checked
against SEC's submissions for each CIK (data.sec.gov, the `sec` site: a declared User-Agent from the SEC_USER_AGENT
secret, under 10 requests a second). Each reply is stored as a raw file before the load, fetched whole every run
(`conditional=False`), so the task never reads the raw bucket back (Plan 05 §0.5 N10). The YAML's bytes are stored as
the `curated` source's raw file the Dow snapshot points to. Then, in one transaction:
- `equity.peer_group` rows (market US) from `us_peer_groups.yaml`;
- each company found by its CIK in `core.entity_identifier` (else by its slug) or created: kind company, slug
  `us-<ticker>`, market US, currency USD, **unpublished**. No run publishes a company or changes a published one's
  flag: the go-live flip is Anand's SQL after US-08's judge (Spec 02A.1 §3);
- identifiers `cik` and `ticker` (source sec) with dated ranges, a change closing the old range where the new one starts
  (India's `plan_identifier`);
- `equity.company`: peer group, valuation family and note, fiscal-year-end month, price exchange (NYSE or NASDAQ);
- `equity.index_membership`: one `dow-jones` snapshot dated the YAML's `as_of`, written when the member set differs from
  the latest stored one.
A second identical load writes nothing.

Checks, B blocking, W warning:
- (B) `submissions`: every company's SEC record read;
- (B) `identity`: each CIK lists the YAML's ticker; it files 10-Ks and no 20-F or 40-F (Spec 02A.1 §9.4);
- (B) `master`: the 30 companies in the master, market US, currency USD; none published by this run;
- (B) `membership`: the latest Dow snapshot holds exactly the YAML's 30;
- (W) `exchange`, (W) `sic_fy`: SEC's exchange, SIC and fiscal-year end against the YAML, each mismatch naming the
  company (Spec 02A.1 §5.1);
- (W) `recent_filing`: a CIK with no 10-K or 10-Q in 200 days (a new holding company may file under a new CIK, as
  ExxonMobil did: Data Audit 02a finding 14).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

from ft.equity import us_universe as us
from ft.equity.guard import equity_due
from ft.equity.universe import IdRange
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register
from ft.jobs.tasks.equity_universe import Close, plan_identifier

NEW_YORK = ZoneInfo("America/New_York")
CURATED_URL = "https://github.com/anandbenny-spec/finance-terminal/blob/main/py/config/equity/us_companies.yaml"


@dataclass
class Fetched:
    today: date
    cfg: us.UsConfig
    curated_raw_id: int
    submissions: dict[str, us.Submission] = field(default_factory=dict)      # ticker -> SEC's record
    failed: list[str] = field(default_factory=list)                          # tickers whose record couldn't be read


def store_curated(fetcher, body: bytes, content_date: date, fetched_at, task_run_id: int) -> int:
    """The curated YAML's bytes as a `curated` raw file (the evidence the Dow snapshot points to), stored once per
    content hash through the storage function, as the shared fetcher stores a download."""
    sha = hashlib.sha256(body).digest()
    source_id = fetcher.db.execute("select id from core.source where key = 'curated'").fetchone()[0]
    have = fetcher.db.execute("select id from core.raw_file where source_id = %s and sha256 = %s",
                              (source_id, sha)).fetchone()
    if have is not None:
        return have[0]
    obj = fetcher.storage().put(body, source="curated", media_type="text/yaml", ext="yaml")
    row = fetcher.db.execute(
        """insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, content_date,
                                      fetched_at, task_run_id)
           values (%s, %s, %s, %s, %s, 'text/yaml', %s, %s, %s)
           on conflict (source_id, sha256) do nothing returning id""",
        (obj.object_key, source_id, CURATED_URL, sha, len(body), content_date, fetched_at, task_run_id)).fetchone()
    if row is not None:
        fetcher.new_files += 1
        return row[0]
    return fetcher.db.execute("select id from core.raw_file where source_id = %s and sha256 = %s",
                              (source_id, sha)).fetchone()[0]


@register
class UsUniverse(Task):
    key = "us.universe"

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        if not db.execute("select exists (select 1 from core.source where key = 'sec')").fetchone()[0]:
            return Due(False, "not set up: migration 0039 (us_market) isn't pushed to this project")
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.company")

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        cfg = us.load_us_config()
        ua = us.sec_user_agent()                     # fails at once, naming the secret, when it's empty
        f = ctx.fetcher
        out = Fetched(ctx.now.astimezone(NEW_YORK).date(), cfg,
                      store_curated(f, us.COMPANIES_FILE.read_bytes(), cfg.as_of, ctx.now, ctx.task_run_id))
        for c in cfg.companies:
            try:
                got = f.get(us.submissions_url(c.cik), source="sec", headers={"User-Agent": ua},
                            media_type="application/json", ext="json", conditional=False)
                out.submissions[c.ticker] = us.parse_submissions(got.body)
            except (FetchError, us.UniverseError) as e:
                out.failed.append(f"{c.ticker} ({e})")
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        written = 0
        sec = tx.execute("select id from core.source where key = 'sec'").fetchone()[0]
        published_before = tx.execute(
            "select count(*) from core.entity where kind = 'company' and market = 'US' and is_published").fetchone()[0]

        have = {r[0]: r[1:] for r in tx.execute(
            "select key, market, label, damodaran_industry, source_note from equity.peer_group where key like 'us-%'")}
        for g in f.cfg.groups.values():
            want = ("US", g.label, g.damodaran_industry, g.source_note)
            if have.get(g.key) != want:
                tx.execute("""insert into equity.peer_group (key, market, label, damodaran_industry, source_note)
                              values (%s, 'US', %s, %s, %s)
                              on conflict (key) do update set market = excluded.market, label = excluded.label,
                                damodaran_industry = excluded.damodaran_industry, source_note = excluded.source_note""",
                           (g.key, g.label, g.damodaran_industry, g.source_note))
                written += 1

        stored: dict[tuple[int, str], list[IdRange]] = {}
        by_cik: dict[str, int] = {}
        for eid, scheme, value, lo, hi in tx.execute(
                """select i.entity_id, i.scheme, i.value, lower(i.valid), upper(i.valid)
                     from core.entity_identifier i join core.entity e on e.id = i.entity_id
                    where e.kind = 'company' and e.market = 'US' and i.scheme in ('cik', 'ticker')""").fetchall():
            stored.setdefault((eid, scheme), []).append(IdRange(value, lo, hi))
            if scheme == "cik" and hi is None:
                by_cik[value] = eid
        by_slug = dict(tx.execute("select slug, id from core.entity where kind = 'company' and market = 'US'").fetchall())
        company = {r[0]: r[1:] for r in tx.execute(
            """select c.entity_id, c.peer_group_key, c.valuation_family, c.family_note, c.fy_end_month, c.price_exchange
                 from equity.company c join core.entity e on e.id = c.entity_id where e.market = 'US'""").fetchall()}

        created, id_changes, members = [], [], {}
        for c in f.cfg.companies:
            cik = str(c.cik)
            eid = by_cik.get(cik) or by_slug.get(c.slug)
            if eid is None:
                eid = tx.execute("""insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
                                    values ('company', %s, %s, %s, 'US', 'USD', false) returning id""",
                                 (c.slug, c.name, c.short_name)).fetchone()[0]
                created.append(c.slug)
                written += 1
            members[eid] = c
            for scheme, current in (("cik", cik), ("ticker", c.ticker)):
                for op in plan_identifier(stored.get((eid, scheme), []), current, [], f.today):
                    if isinstance(op, Close):
                        tx.execute("""update core.entity_identifier set valid = daterange(%s, %s, '[)')
                                       where entity_id = %s and scheme = %s and value = %s and upper_inf(valid)
                                         and lower(valid) is not distinct from %s""",
                                   (op.start, op.end, eid, scheme, op.value, op.start))
                        id_changes.append(f"{c.slug} {scheme} {op.value} ended {op.end}")
                    else:
                        tx.execute("""insert into core.entity_identifier (entity_id, scheme, value, valid, source_id)
                                      values (%s, %s, %s, daterange(%s, %s, '[)'), %s)""",
                                   (eid, scheme, op.range.value, op.range.start, op.range.end, sec))
                    written += 1
            row = (c.peer_group, c.valuation_family, c.family_note, c.fy_end_month, c.exchange)
            if company.get(eid) != row:
                tx.execute("""insert into equity.company (entity_id, peer_group_key, valuation_family, family_note,
                                                          fy_end_month, price_exchange)
                              values (%s, %s, %s, %s, %s, %s)
                              on conflict (entity_id) do update set peer_group_key = excluded.peer_group_key,
                                  valuation_family = excluded.valuation_family, family_note = excluded.family_note,
                                  fy_end_month = excluded.fy_end_month, price_exchange = excluded.price_exchange""",
                           (eid, *row))
                written += 1

        index_id = tx.execute("select id from core.entity where kind = 'index' and slug = %s",
                              (f.cfg.index,)).fetchone()
        if index_id is None:
            raise us.UniverseError(f"no core.entity for the index {f.cfg.index}")
        prior = {r[0] for r in tx.execute(
            """select entity_id from equity.index_membership where index_entity_id = %(i)s and snapshot_date =
               (select max(snapshot_date) from equity.index_membership where index_entity_id = %(i)s)""",
            {"i": index_id[0]}).fetchall()}
        change = None
        if set(members) != prior:
            change = (f"added {len(set(members) - prior)}, removed {len(prior - set(members))}" if prior else "first snapshot")
            with tx.cursor() as cur:
                cur.executemany("""insert into equity.index_membership (index_entity_id, snapshot_date, entity_id, raw_file_id)
                                   values (%s, %s, %s, %s) on conflict do nothing""",
                                [(index_id[0], f.cfg.as_of, e, f.curated_raw_id) for e in sorted(members)])
            written += len(members)

        self.result = {"created": created, "id_changes": id_changes, "members": members, "index_id": index_id[0],
                       "membership_change": change, "published_before": published_before}
        return written

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        r, out = self.result, []
        n = len(f.cfg.companies)
        out.append(Check("submissions", not f.failed,
                         ("SEC's record couldn't be read for: " + "; ".join(f.failed)) if f.failed
                         else f"{len(f.submissions)} of {n} companies' SEC records read"))

        found = {t: us.compare(c, f.submissions[t], f.today) for c in f.cfg.companies if (t := c.ticker) in f.submissions}
        blocking = [b for x in found.values() for b in x.blocking]
        out.append(Check("identity", not blocking, "; ".join(blocking) or
                         f"{len(found)} CIKs list their ticker and file 10-Ks, none a 20-F or 40-F"))

        ids = sorted(r["members"])
        master = tx.execute("""select count(*) filter (where e.market = 'US' and e.currency = 'USD' and e.kind = 'company'
                                                     and c.entity_id is not null),
                                      count(*) filter (where e.is_published)
                                 from core.entity e left join equity.company c on c.entity_id = e.id
                                where e.id = any(%s)""", (ids,)).fetchone()
        published_now = tx.execute("""select count(*) from core.entity
                                       where kind = 'company' and market = 'US' and is_published""").fetchone()[0]
        bad = []
        if master[0] != n:
            bad.append(f"{master[0]} of {n} companies are in the master as US/USD companies")
        if published_now != r["published_before"]:
            bad.append(f"published US companies went from {r['published_before']} to {published_now}")
        out.append(Check("master", not bad, "; ".join(bad) or
                         f"{n} US companies in the master, {master[1]} published (the flip is Anand's)"
                         + (f"; created {len(r['created'])}" if r["created"] else "")
                         + (f"; identifier changes: {', '.join(r['id_changes'])}" if r["id_changes"] else "")))

        latest = {e for (e,) in tx.execute(
            """select entity_id from equity.index_membership where index_entity_id = %(i)s and snapshot_date =
               (select max(snapshot_date) from equity.index_membership where index_entity_id = %(i)s)""",
            {"i": r["index_id"]}).fetchall()}
        out.append(Check("membership", latest == set(ids),
                         f"the latest {f.cfg.index} snapshot holds {len(latest & set(ids))} of the {n} and "
                         f"{len(latest - set(ids))} others" if latest != set(ids)
                         else f"{f.cfg.index}: {len(latest)} members as of {f.cfg.as_of.isoformat()}"
                         + (f" ({r['membership_change']})" if r["membership_change"] else "")))

        def warn(name: str, pick, ok: str) -> Check:
            items = [w for x in found.values() for w in x.warnings if pick(w)]
            return Check(name, not items, "; ".join(items) or ok, blocking=False)

        out.append(warn("exchange", lambda w: " at SEC, " in w and "listed on" in w,
                        "every listing exchange matches SEC's"))
        out.append(warn("sic_fy", lambda w: w.split(": ", 1)[1].startswith(("SIC", "fiscal year")),
                        "every SIC and fiscal-year end matches SEC's"))
        out.append(warn("recent_filing", lambda w: "no 10-K or 10-Q filed" in w,
                        "every CIK filed a 10-K or 10-Q in the last 200 days"))
        return out
