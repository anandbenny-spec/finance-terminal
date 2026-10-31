"""equity.statements: every registered results XBRL filing parsed into `equity.financials` (Spec 02A §5.4; job 2A-09).
Database only: it reads the stored instances through the storage function (`ctx.fetcher.read`: every read checks the
object's hash) and fetches nothing.

A run parses the filings whose `parse_status` is `pending` (registered by `equity.filings`, 2A-07) or whose
`parser_version` is older than `ft.equity.statements.PARSER_VERSION` (re-parsing replaces that filing's rows). Each
gets `parse_status` (`parsed` or `failed`), `parse_note` (what the parse did, or the failed check and why) and
`parser_version`. A filing that fails a check gets no rows: its figures are never stored (`ft/equity/statements.py`
lists the checks). Insurers file no face value: the newest stored EQUITY_L (from `equity.universe`) supplies it,
noted in the row's tags.

Checks, B blocking, W warning:
- (W) `filings_failed`: every filing that failed, named with its reason;
- (B) `failed_share`: no more than 5% of the run's filings failed;
- (B) `latest_quarters`: no universe company's latest quarter failed. A file that failed `ytd_only` isn't counted: it
  has no quarter column, so it isn't a quarter that failed (Anand, 31 Oct 2026, #114: Coal India's 30 Sep 2026 pair
  carries only the half-year; it still fails, and `filings_failed` names it);
- (B) `reference`: when the audit's reference file is here (`FT_EQUITY_REFERENCE`, or the fixtures checkout's
  `equity/audit/reference_values.csv`), X1's FY2026 figures for every reference filing that is registered equal the
  stored rows exactly (Spec 02A §5.4: blocking on the first backfill, then a test; the file lives in the private
  fixtures repo, so on the workflows' runners the check says it didn't run, and the test suite runs it);
- (W) `parsed`: what the run parsed (filings, rows by period kind), always passing.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ft.equity import statements as st
from ft.equity import universe as u
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 12
FAILED_SHARE = Decimal("0.05")
REFERENCE_ENV = "FT_EQUITY_REFERENCE"
REFERENCE_DEFAULT = Path(__file__).resolve().parents[5] / "data" / "fixtures" / "equity" / "audit" / "reference_values.csv"


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Item:
    document_id: int
    entity_id: int
    slug: str
    filing: st.Filing
    parsed: st.Parsed | None = None
    error: st.StatementError | None = None


@dataclass
class Fetched:
    items: list[Item] = field(default_factory=list)
    face_values: str = ""                 # what supplied insurers' face values


def reference_path() -> Path | None:
    p = os.environ.get(REFERENCE_ENV)
    path = Path(p) if p else REFERENCE_DEFAULT
    return path if path.is_file() else None


@register
class EquityStatements(Task):
    key = "equity.statements"
    version = st.PARSER_VERSION            # filings parsed by an older version are parsed again
    requires = "equity.financials"         # the production guard's table (ft/equity/guard.py)

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (no transaction; reads only)
    def fetch(self, ctx: Context) -> Fetched:
        f = ctx.fetcher
        rows = f.db.execute(
            """select fl.document_id, fl.entity_id, e.slug, d.external_key, fl.basis, fl.format, fl.period_end,
                      fl.fy_start, d.raw_file_id, c.isin
                 from equity.filing fl join core.document d on d.id = fl.document_id
                 join core.entity e on e.id = fl.entity_id
                 left join equity.company c on c.entity_id = fl.entity_id
                where fl.parse_status = 'pending'
                   or (case when fl.parser_version ~ '^[0-9]+$' then fl.parser_version::int else 0 end) < %s
                order by e.slug, fl.period_end, fl.basis, d.external_key""", (self.version,)).fetchall()
        out = Fetched()
        needs_face = any(r[5] in st.INSURERS for r in rows)
        faces, out.face_values = self._face_values(ctx) if needs_face else ({}, "")
        for doc_id, entity_id, slug, name, basis, fmt, period_end, fy_start, raw_id, isin in rows:
            item = Item(doc_id, entity_id, slug, st.Filing(name, basis, fmt, period_end, fy_start))
            if raw_id is None:
                item.error = st.StatementError("unreadable", "the document has no stored file")
            else:
                body = f.read(raw_id)               # a storage or hash error fails the task: nothing is marked
                try:
                    item.parsed = st.parse_filing(body, item.filing, face_value=faces.get(isin))
                except st.StatementError as e:
                    item.error = e
            out.items.append(item)
        return out

    def _face_values(self, ctx: Context) -> tuple[dict[str, Decimal], str]:
        """Face value by ISIN from the newest EQUITY_L the universe task stored (insurers file none)."""
        f = ctx.fetcher
        got = f.db.execute("""select id, fetched_at from core.raw_file where source_url = %s
                               order by fetched_at desc, id desc limit 1""", (u.EQUITY_L_URL,)).fetchone()
        if got is None:
            return {}, "no EQUITY_L is stored, so insurers have no face value"
        out: dict[str, Decimal] = {}
        for isin, row in u.parse_equity_l(f.read(got[0])).items():
            try:
                v = Decimal(row.face_value)
            except InvalidOperation:
                continue
            if v > 0:
                out[isin] = v
        return out, f"insurers' face value from EQUITY_L fetched {got[1].date().isoformat()}"

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        cols = st.COLUMNS
        insert = (f"""insert into equity.financials (document_id, entity_id, basis, period_start, period_end,
                                                     period_kind, currency, {", ".join(cols)}, tags)
                      values (%s, %s, %s, %s, %s, %s, 'INR', {", ".join(["%s"] * len(cols))}, %s)""")
        n = 0
        for it in f.items:
            tx.execute("delete from equity.financials where document_id = %s", (it.document_id,))
            if it.parsed is not None:
                for r in it.parsed.rows:
                    tx.execute(insert, (it.document_id, it.entity_id, it.filing.basis, r.period_start, r.period_end,
                                        r.period_kind, *[r.values.get(c) for c in cols],
                                        json.dumps(r.tags, ensure_ascii=False)))
                    n += 1
                status, note = "parsed", f"v{self.version}: {it.parsed.note}"
            else:
                status, note = "failed", f"v{self.version}: {it.error}"
            tx.execute("""update equity.filing set parse_status = %s, parse_note = %s, parser_version = %s
                           where document_id = %s""", (status, note, str(self.version), it.document_id))
        return n

    # ------------------------------------------------------------------ checks
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        items = f.items
        failed = [it for it in items if it.error is not None]
        named = [f"{it.slug} {it.filing.period_end.isoformat()} {it.filing.basis} ({it.filing.file_name}): {it.error}"
                 for it in failed]
        out = [Check("filings_failed", not failed, ("failed, so no rows: " + _names(named)) if failed
                     else "no filing failed", blocking=False)]
        share = Decimal(len(failed)) / len(items) if items else Decimal(0)
        out.append(Check("failed_share", share <= FAILED_SHARE,
                         f"{len(failed)} of {len(items)} filings failed ({share:.1%}; at most 5%)" if items
                         else "no filing to parse"))
        out.append(self._latest_quarters(tx, failed))
        out.append(self._reference(tx))
        kinds: dict[str, int] = {}
        for it in items:
            for r in (it.parsed.rows if it.parsed else []):
                kinds[r.period_kind] = kinds.get(r.period_kind, 0) + 1
        self.result = {"filings": len(items), "parsed": len(items) - len(failed), "failed": len(failed),
                       "rows": kinds, "face_values": f.face_values}
        out.append(Check("parsed", True, f"{len(items) - len(failed)} of {len(items)} filings parsed; rows by period "
                                         "kind: " + (", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "none")
                         + (f"; {f.face_values}" if f.face_values else ""), blocking=False))
        return out

    @staticmethod
    def _latest_quarters(tx, failed: list[Item]) -> Check:
        """A universe company's latest quarter (its newest period end with a registered filing) failed in this run. A
        `ytd_only` failure isn't counted (Anand, 31 Oct 2026, #114): the file has no quarter column, so the check, which
        exists to stop a parser fault from dropping a company's newest quarter, has no quarter to protect there. The
        failure is still named by `filings_failed`."""
        bad = []
        for it in failed:
            if it.error is not None and it.error.check == "ytd_only":
                continue
            latest = tx.execute("""select max(fl.period_end) from equity.filing fl
                                    where fl.entity_id = %s
                                      and exists (select 1 from equity.company c where c.entity_id = fl.entity_id)""",
                                (it.entity_id,)).fetchone()[0]
            if latest is not None and it.filing.period_end == latest:
                bad.append(f"{it.slug} {latest.isoformat()} {it.filing.basis} ({it.error})")
        return Check("latest_quarters", not bad, ("a company's latest quarter failed: " + _names(bad)) if bad
                     else "no company's latest quarter failed")

    @staticmethod
    def _reference(tx) -> Check:
        path = reference_path()
        if path is None:
            return Check("reference", True, "not run here: the reference file lives in the private fixtures repo "
                                            "(the test suite runs it)")
        refs = st.load_reference(path)
        names = sorted({r.file_name for r in refs})
        stored = tx.execute(
            """select d.external_key, c.nse_symbol, fl.basis, fl.parse_status, fl.parse_note, fl.document_id
                 from core.document d join equity.filing fl on fl.document_id = d.id
                 join equity.company c on c.entity_id = fl.entity_id
                where d.doc_type = 'results_xbrl' and d.external_key = any(%s)
                  and d.version_no = (select max(d2.version_no) from core.document d2
                                       where d2.source_id = d.source_id and d2.external_key = d.external_key)""",
            (names,)).fetchall()
        by = {(r[0], r[1], r[2]): r for r in stored}
        diffs, checked = [], 0
        for ref in refs:
            got = by.get((ref.file_name, ref.symbol, ref.basis))
            if got is None:
                continue                            # not registered here: nothing to compare
            checked += 1
            if got[3] != "parsed":
                diffs.append(f"{ref.symbol} {ref.basis} ({ref.file_name}): {got[3]}, {got[4]}")
                continue
            rows, tags = {}, {}
            for kind, tag, *vals in tx.execute(
                    f"""select period_kind, tags, {", ".join(st.REFERENCE_COLUMNS)} from equity.financials
                         where document_id = %s and period_end = %s""", (got[5], ref.period_end)).fetchall():
                rows[kind], tags[kind] = dict(zip(st.REFERENCE_COLUMNS, vals, strict=True)), tag or {}
            diffs += [f"{ref.symbol} {ref.basis} ({ref.file_name}) {d}"
                      for d in st.reference_differences(ref, rows, tags)]
        return Check("reference", not diffs, ("differs from X1's reference: " + _names(diffs)) if diffs
                     else f"{checked} reference filings equal X1's FY2026 figures exactly"
                          + ("" if checked else " (none of them is registered here)"))
