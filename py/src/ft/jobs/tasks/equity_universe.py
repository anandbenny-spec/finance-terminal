"""equity.universe: the company master, index membership and curated peer groups (Spec 02A §4, §6; job 2A-04).

Weekly, under the NSE posture (CLAUDE.md §1): the five Nifty lists, EQUITY_L and symbolchange.csv from NSE's archive
and BSE's scrip list, each stored as a raw file before the load. Then, in one transaction:
- every company in the universe index's list (`config/equity/universe.yaml`: nifty-50 now) is found by ISIN in
  `core.entity_identifier` (else by its NSE symbol, which means its ISIN changed) or created (kind company, market IN,
  currency INR, unpublished: 2A-06 publishes a company once its first price load passes); its ISIN, NSE symbol
  and BSE code are identifiers with dated ranges: a change closes the old range where the new one starts and never
  edits a value in place (symbol ranges come from symbolchange.csv, which gives the effective dates);
- `equity.company` holds the current identifiers, the Nifty list's industry, the listing date and the curated
  classification (`companies.yaml`); a company that leaves the list keeps its rows;
- `equity.index_membership`: a dated snapshot of an index only when its member set differs from the latest stored
  one (the first run writes all five). Only companies in the master can be members (the table references
  `core.entity`), so a Nifty 100 snapshot holds the master's companies that are in the list; the files' own row
  counts are checked in full;
- `equity.peer_group` from `peer_groups.yaml`.
A second identical load writes nothing.

Checks, B blocking, W warning:
- (B) `index_files`: each list has the row count `universe.yaml` gives (the Nifty 50 file has 50);
- (B) `nifty200_union`: the Nifty 200 is the Nifty 100 plus the Midcap 100 (U1's rule, Data Audit 02a);
- (B) `in_equity_l`: every universe ISIN is in EQUITY_L;
- (B) `curated`: every universe company has a peer group and a valuation family in the YAML;
- (W) `bse_codes`: universe ISINs missing from BSE's list (BSE Ltd, which lists only on NSE, is expected);
- (W) `membership_changes`: an index whose members changed since its last snapshot, with the names.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from ft.equity import universe as u
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

INDIA = ZoneInfo("Asia/Kolkata")


@dataclass
class ListFile:
    key: str
    file: str
    expected_rows: int
    raw_file_id: int
    rows: list[u.NiftyRow]


@dataclass
class Fetched:
    snapshot: date
    lists: dict[str, ListFile]
    equity_l: dict[str, u.EquityL]
    changes: list[u.SymbolChange]
    bse: dict[str, u.BseScrip] | None            # None: BSE's list couldn't be fetched this run
    bse_note: str
    cfg: u.UniverseConfig
    groups: dict[str, u.PeerGroup]
    curation: dict[str, u.Curation]


# ------------------------------------------------------------------------------------------------ identifier plan
@dataclass(frozen=True)
class Close:
    value: str
    start: date | None
    end: date


@dataclass(frozen=True)
class Insert:
    range: u.IdRange


def plan_identifier(existing: list[u.IdRange], current: str, history: list[u.IdRange], today: date) -> list:
    """What to write so one entity's identifier of one scheme reads right, given its stored ranges: close the open
    range of any other value where the change happened (history's date, else today), open `current` where it began
    (history's date, else today if the entity had an identifier before, else unbounded), and add history ranges not
    stored yet. Never edits a value; no change gives no operations."""
    ops: list = []
    ends = {h.value: h.end for h in history if h.end is not None}
    for r in existing:
        if r.end is None and r.value != current:
            end = ends.get(r.value) or today
            if r.start is not None and end <= r.start:
                end = r.start + timedelta(days=1)
            ops.append(Close(r.value, r.start, end))
    known = {r.value for r in existing}
    if current not in known or not any(r.value == current and r.end is None for r in existing):
        h = next((h for h in history if h.value == current), None)
        start = h.start if h is not None and h.start is not None else (today if existing else None)
        ops.append(Insert(u.IdRange(current, start, None)))
    for h in history:
        if h.value != current and h.value not in known:
            ops.append(Insert(h))
    return ops


def _open_at(lo: date | None, hi: date | None, day: date) -> bool:
    return (lo is None or lo <= day) and (hi is None or day < hi)


# ------------------------------------------------------------------------------------------------ the task
@register
class EquityUniverse(Task):
    key = "equity.universe"

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.company")

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher
        cfg = u.load_universe_config()
        groups = u.load_peer_groups()
        curation = u.load_companies(groups)

        def get(url: str, source: str, media_type: str, **kw) -> tuple[int, bytes]:
            got = f.get(url, source=source, media_type=media_type, **kw)
            return got.raw_file_id, (got.body if got.body is not None else f.read(got.raw_file_id))

        lists = {}
        for key, idx in cfg.indices.items():
            raw_id, body = get(u.nifty_url(idx.file), "nse", "text/csv", headers=u.NSE_HEADERS)
            lists[key] = ListFile(key, idx.file, idx.rows, raw_id, u.parse_nifty_list(body))
        _, body = get(u.EQUITY_L_URL, "nse", "text/csv", headers=u.NSE_HEADERS)
        equity_l = u.parse_equity_l(body)
        _, body = get(u.SYMBOLCHANGE_URL, "nse", "text/csv", headers=u.NSE_HEADERS)
        changes = u.parse_symbolchange(body)
        bse, note = None, ""
        try:
            _, body = get(u.BSE_SCRIPS_URL, "bse", "application/json", params=u.BSE_SCRIPS_PARAMS,
                          headers=u.BSE_HEADERS)
            bse, dup = u.parse_bse_scrips(body)
            note = f"{len(bse)} scrips" + (f"; {len(dup)} ISINs have more than one code" if dup else "")
        except (FetchError, u.UniverseError) as e:
            note = f"BSE's scrip list couldn't be read: {e}"
        return Fetched(ctx.now.astimezone(INDIA).date(), lists, equity_l, changes, bse, note, cfg, groups, curation)

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        written = 0
        snap = f.snapshot
        src = dict(tx.execute("select key, id from core.source where key in ('nse', 'bse')").fetchall())

        # peer groups
        have = {r[0]: r[1:] for r in tx.execute(
            "select key, market, label, damodaran_industry, source_note from equity.peer_group").fetchall()}
        for g in f.groups.values():
            want = ("IN", g.label, g.damodaran_industry, g.source_note)
            if have.get(g.key) != want:
                tx.execute("""insert into equity.peer_group (key, market, label, damodaran_industry, source_note)
                              values (%s, 'IN', %s, %s, %s)
                              on conflict (key) do update set label = excluded.label,
                                damodaran_industry = excluded.damodaran_industry, source_note = excluded.source_note""",
                           (g.key, g.label, g.damodaran_industry, g.source_note))
                written += 1

        # stored identifiers of every company entity
        stored: dict[tuple[int, str], list[u.IdRange]] = {}
        by_isin: dict[str, int] = {}
        by_symbol: dict[str, int] = {}
        for eid, scheme, value, lo, hi in tx.execute(
                """select i.entity_id, i.scheme, i.value, lower(i.valid), upper(i.valid)
                     from core.entity_identifier i join core.entity e on e.id = i.entity_id
                    where e.kind = 'company' and i.scheme in ('isin', 'nse_symbol', 'bse_code')""").fetchall():
            stored.setdefault((eid, scheme), []).append(u.IdRange(value, lo, hi))
            if _open_at(lo, hi, snap):
                if scheme == "isin":
                    by_isin[value] = eid
                elif scheme == "nse_symbol":
                    by_symbol[value] = eid
        slugs = dict(tx.execute("select id, slug from core.entity where kind = 'company'").fetchall())
        company = {r[0]: r[1:] for r in tx.execute(
            """select entity_id, isin, nse_symbol, bse_code, nse_industry, peer_group_key, valuation_family,
                      family_note, fy_end_month, price_exchange, listed_on from equity.company""").fetchall()}

        created, id_changes, curated_missing, universe = [], [], [], []
        for r in f.lists[f.cfg.index].rows:
            eid = by_isin.get(r.isin) or by_symbol.get(r.symbol)
            if eid is None:
                slug = u.slugify(r.symbol)
                if slug in slugs.values():
                    raise u.UniverseError(f"{r.symbol}: the slug {slug!r} belongs to another company")
                eid = tx.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                                    values ('company', %s, %s, 'IN', 'INR', false) returning id""",
                                 (slug, r.name)).fetchone()[0]
                slugs[eid] = slug
                created.append(slug)
                written += 1
            by_isin[r.isin], by_symbol[r.symbol] = eid, eid
            universe.append((eid, r))

            bse = f.bse.get(r.isin) if f.bse is not None else None
            wanted = [("isin", r.isin, [], "nse"), ("nse_symbol", r.symbol, u.symbol_history(r.symbol, f.changes), "nse")]
            if bse is not None:
                wanted.append(("bse_code", bse.code, [], "bse"))
            for scheme, current, history, source in wanted:
                ops = plan_identifier(stored.get((eid, scheme), []), current, history, snap)
                for op in ops:
                    if isinstance(op, Close):
                        tx.execute("""update core.entity_identifier set valid = daterange(%s, %s, '[)')
                                       where entity_id = %s and scheme = %s and value = %s and upper_inf(valid)
                                         and lower(valid) is not distinct from %s""",
                                   (op.start, op.end, eid, scheme, op.value, op.start))
                        id_changes.append(f"{slugs[eid]} {scheme} {op.value} ended {op.end}")
                    else:
                        rg = op.range
                        tx.execute("""insert into core.entity_identifier (entity_id, scheme, value, valid, source_id)
                                      values (%s, %s, %s, daterange(%s, %s, '[)'), %s)""",
                                   (eid, scheme, rg.value, rg.start, rg.end, src[source]))
                    written += 1
                if ops:
                    cur = [x for x in stored.get((eid, scheme), [])]
                    for op in ops:
                        if isinstance(op, Close):
                            cur = [u.IdRange(x.value, x.start, op.end) if x.value == op.value and x.end is None
                                   and x.start == op.start else x for x in cur]
                        else:
                            cur.append(op.range)
                    stored[(eid, scheme)] = cur

            cur_c = f.curation.get(slugs[eid])
            if cur_c is None:
                curated_missing.append(slugs[eid])
            eql = f.equity_l.get(r.isin)
            old = company.get(eid)
            row = (r.isin, r.symbol, bse.code if bse is not None else (old[2] if old else None), r.industry,
                   cur_c.peer_group if cur_c else None, cur_c.valuation_family if cur_c else "operating",
                   cur_c.family_note if cur_c else None, cur_c.fy_end_month if cur_c else 3,
                   cur_c.price_exchange if cur_c else "NSE",
                   eql.listing_date if eql is not None and eql.listing_date else (old[9] if old else None))
            if old is None or tuple(old) != row:
                tx.execute("""insert into equity.company (entity_id, isin, nse_symbol, bse_code, nse_industry,
                                  peer_group_key, valuation_family, family_note, fy_end_month, price_exchange, listed_on)
                              values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                              on conflict (entity_id) do update set isin = excluded.isin,
                                  nse_symbol = excluded.nse_symbol, bse_code = excluded.bse_code,
                                  nse_industry = excluded.nse_industry, peer_group_key = excluded.peer_group_key,
                                  valuation_family = excluded.valuation_family, family_note = excluded.family_note,
                                  fy_end_month = excluded.fy_end_month, price_exchange = excluded.price_exchange,
                                  listed_on = excluded.listed_on""", (eid, *row))
                written += 1

        # index membership: a snapshot only when the member set changed
        changes = []
        for key, lf in f.lists.items():
            index_id = tx.execute("select id from core.entity where kind = 'index' and slug = %s", (key,)).fetchone()
            if index_id is None:
                raise u.UniverseError(f"no core.entity for the index {key}")
            members = {by_isin[x.isin] for x in lf.rows if x.isin in by_isin}
            prior = {r[0] for r in tx.execute(
                """select entity_id from equity.index_membership where index_entity_id = %(i)s and snapshot_date =
                   (select max(snapshot_date) from equity.index_membership where index_entity_id = %(i)s)""",
                {"i": index_id[0]}).fetchall()}
            if members != prior:
                if prior:
                    changes.append(f"{key}: added {', '.join(sorted(slugs[e] for e in members - prior)) or 'none'}; "
                                   f"removed {', '.join(sorted(slugs[e] for e in prior - members)) or 'none'}")
                with tx.cursor() as cur:
                    cur.executemany("""insert into equity.index_membership (index_entity_id, snapshot_date, entity_id,
                                           raw_file_id) values (%s, %s, %s, %s) on conflict do nothing""",
                                    [(index_id[0], snap, e, lf.raw_file_id) for e in sorted(members)])
                written += len(members)

        self.result = {"created": created, "id_changes": id_changes, "curated_missing": curated_missing,
                       "membership_changes": changes, "universe": universe}
        return written

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        r = self.result
        out: list[Check] = []

        wrong = [f"{lf.key} has {len(lf.rows)} rows, not {lf.expected_rows}" for lf in f.lists.values()
                 if len(lf.rows) != lf.expected_rows]
        out.append(Check("index_files", not wrong, "; ".join(wrong) or ", ".join(
            f"{lf.key} {len(lf.rows)}" for lf in f.lists.values())))

        isins = {k: {x.isin for x in lf.rows} for k, lf in f.lists.items()}
        if {"nifty-100", "nifty-200", "nifty-midcap-100"} <= isins.keys():
            union = isins["nifty-100"] | isins["nifty-midcap-100"]
            extra, missing = sorted(isins["nifty-200"] - union), sorted(union - isins["nifty-200"])
            out.append(Check("nifty200_union", not extra and not missing,
                             f"Nifty 200 has {len(extra)} ISINs outside Nifty 100 + Midcap 100 {extra[:5]}, "
                             f"and lacks {len(missing)} of them {missing[:5]}" if extra or missing
                             else "the Nifty 200 is the Nifty 100 plus the Midcap 100"))

        universe = [x for _, x in r["universe"]]
        absent = [f"{x.symbol} ({x.isin})" for x in universe if x.isin not in f.equity_l]
        out.append(Check("in_equity_l", not absent, ("not in EQUITY_L: " + ", ".join(absent)) if absent
                         else f"{len(universe)} universe ISINs are in EQUITY_L"))

        out.append(Check("curated", not r["curated_missing"],
                         ("no peer group or valuation family in companies.yaml for: "
                          + ", ".join(r["curated_missing"])) if r["curated_missing"]
                         else f"{len(universe)} universe companies are curated"))

        if f.bse is None:
            out.append(Check("bse_codes", False, f.bse_note, blocking=False))
        else:
            missing = [x.symbol for x in universe if x.isin not in f.bse and x.symbol not in f.cfg.bse_not_listed]
            expected = [x.symbol for x in universe if x.isin not in f.bse and x.symbol in f.cfg.bse_not_listed]
            out.append(Check("bse_codes", not missing,
                             (("not in BSE's list: " + ", ".join(missing)) if missing
                              else "every universe ISIN has a BSE code")
                             + (f" (expected without one: {', '.join(expected)})" if expected else "")
                             + f"; {f.bse_note}", blocking=False))

        out.append(Check("membership_changes", not r["membership_changes"],
                         "; ".join(r["membership_changes"]) or "no index changed since its last snapshot",
                         blocking=False))
        return out
