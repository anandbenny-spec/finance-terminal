"""equity.guidance_scores: every management target of a published guidance extraction scored against the company's filed
figures, as `equity.guidance_outcome` rows, plus the guidance series and their `core.forecast` rows (Spec 02B §5.10, §3.4,
§0 decisions 2-3, §10 decisions 6-11 and 15; Spec 02B.1 C5; Plan 04b P8; job 2B-14, #177). The rules are in
`ft/equity/calls/scoring.py`; the map is `py/config/equity/guidance_metrics.yaml`'s `lines`.

Database only, no AI and no fetch. It reads only `speaker_role = 'management'` rows (C5), so a statement whose speaker's role
couldn't be read gets no outcome (the database refuses one, 0028), no series and no forecast, and is never a first statement
or a revision. Duplicates (2B-13 marks them) get no outcome either.

A run recomputes every outcome and writes only what changed: a new row inserted, a changed one updated, an outcome whose
statement is no longer scored (its output superseded) deleted (outcomes are re-made when a rule changes: the D table). So a
re-run on unchanged data writes nothing. The hit rates and the n >= 5 rule are `api.guidance_hit_rates`' (0027), not this
task's.

**Forecasts** (§3.4, §5.9; Plan 04b P8): one series `guidance.<company-slug>.<metric>` per company and metric (kind
`forecast`, source `nse` so the NSE switch hides it, frequency `irregular`, unit `percent` or `inr`), created at its first
point or range target, with one `core.forecast` row per such target (method `ai_extracted`, the quote's span, the vintage the
call date). Only targets that fit the map's line and its basis (not those the statement alone leaves unscored) get one; ₹
targets are stored in full rupees, as the filed figures are. Two targets of one metric, call and period keep the first in
document order (named). The series are published while `ai.guidance_extraction` is shown or labelled and unpublished
otherwise (§10 decision 15: `api.forecasts` shows every forecast of a visible series).

Checks, B blocking, W warning:
- (B) `inputs`: every outcome's inputs give its outcome, relation, actual and base again on their own (`scoring.recompute`);
- (B) `management_only`: no outcome and no forecast for a statement whose speaker's role couldn't be read (C5's two counts);
- (W) `not_scored`: targets not scored, counted by reason (always passes);
- (W) `definition`: companies whose definition check failed, named with the median and n;
- (W) `units`: a filed fraction outside 0 to 1 (held, never rescaled), named;
- (W) `forecasts`: targets left without a forecast row because another of the same metric, call and period has it, named;
- (W) `scored`: outcomes by type and by outcome, counted targets and companies with n >= 5 (always passes).
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ft.equity import metrics as mx
from ft.equity import statements as st
from ft.equity.calls import scoring as sc
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 12
IST = ZoneInfo("Asia/Kolkata")
SERIES_UNITS = {"pct": "percent", "inr": "inr"}


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


def _json(inputs: dict) -> dict:
    return json.loads(json.dumps(inputs, ensure_ascii=False, default=str))


@dataclass(frozen=True)
class Row:
    """What a forecast row needs from a guidance row besides its statement."""
    ai_output_id: int
    document_id: int
    char_start: int
    char_end: int
    metric_label: str | None


@dataclass
class Fetched:
    as_of: date
    scope_ids: list[int] | None
    shown: bool
    nse_id: int | None
    companies: dict[int, sc.Company] = field(default_factory=dict)
    entities: dict[int, tuple[str, str, str | None]] = field(default_factory=dict)   # id -> (name, market, currency)
    statements: dict[int, list[sc.Statement]] = field(default_factory=dict)
    rows: dict[int, Row] = field(default_factory=dict)
    outcomes: list[sc.Outcome] = field(default_factory=list)
    unscored: list[int] = field(default_factory=list)          # statements of companies with no equity.company row
    map: sc.Map | None = None


@register
class EquityGuidanceScores(Task):
    key = "equity.guidance_scores"
    requires = "equity.guidance_outcome"       # the production guard's table (0027)
    scope: frozenset[str] | None = None        # company slugs (tests); None: every company

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (no transaction; reads only)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        m = sc.load_map()
        scope_ids = None
        if self.scope is not None:
            scope_ids = [i for (i,) in db.execute("select id from core.entity where slug = any(%s)",
                                                  (sorted(self.scope),)).fetchall()]
        where, args = ("and g.entity_id = any(%s)", (scope_ids,)) if scope_ids is not None else ("", ())
        shown = bool(db.execute("""select coalesce(bool_or(status in ('shown','labelled')), false) from core.field_policy
                                    where field_key = 'ai.guidance_extraction'""").fetchone()[0])
        nse = db.execute("select id from core.source where key = 'nse'").fetchone()
        f = Fetched(ctx.as_of or ctx.now.astimezone(IST).date(), scope_ids, shown, nse[0] if nse else None, map=m)
        for r in db.execute(
                f"""select g.id, g.entity_id, g.call_event_id, g.metric, g.kind, g.value, g.low, g.high, g.unit, g.direction,
                           g.comparison, g.period_kind, g.target_start, g.target_end, g.basis, g.growth_basis, g.scope_text,
                           g.vintage_date, g.not_scored_reason, g.metric_text, g.period_text,
                           (select t->>'quote' from jsonb_array_elements(o.content->'targets') t
                             where t->>'citation_no' = g.citation_no::text limit 1),
                           g.ai_output_id, g.document_id, g.char_start, g.char_end, g.metric_label
                      from equity.guidance g
                      join core.ai_output o on o.id = g.ai_output_id and o.task = 'guidance_extraction'
                                           and o.status = 'published'
                     where g.speaker_role = 'management' and g.duplicate_of is null
                       and g.entity_id in (select id from core.entity where market = 'IN') {where}
                     order by g.id""", args).fetchall():
            s = sc.Statement(*r[:22])
            f.statements.setdefault(s.entity_id, []).append(s)
            f.rows[s.id] = Row(*r[22:])
        ids = sorted(f.statements)
        if not ids:
            return f
        for eid, slug, name, family, fy, market, currency in db.execute(
                """select c.entity_id, e.slug, e.name, c.valuation_family, c.fy_end_month, e.market, e.currency
                     from equity.company c join core.entity e on e.id = c.entity_id
                    where c.entity_id = any(%s)""", (ids,)).fetchall():
            f.companies[eid] = sc.Company(eid, slug, family, fy)
            f.entities[eid] = (name, market, currency)
        f.unscored = [s.id for eid in ids if eid not in f.companies for s in f.statements[eid]]
        ids = sorted(f.companies)
        for eid, formats in db.execute("""select entity_id, array_agg(distinct format) from equity.filing
                                           where entity_id = any(%s) and parse_status = 'parsed' group by 1""",
                                       (ids,)).fetchall():
            f.companies[eid].formats = frozenset(formats)
        cols = st.COLUMNS
        for row in db.execute(
                f"""select x.entity_id, x.document_id, x.basis, x.period_kind, x.period_start, x.period_end, x.tags,
                           fl.format, fl.filed_at, {", ".join(cols)}
                      from equity.financials x join equity.filing fl on fl.document_id = x.document_id
                     where x.entity_id = any(%s) and fl.parse_status = 'parsed'""", (ids,)).fetchall():
            eid, doc, basis, kind, start, end, tags, fmt, filed_at, *vals = row
            values = {c: v for c, v in zip(cols, vals, strict=True) if v is not None}
            f.companies[eid].fins.append(mx.Fin(doc, basis, kind, start, end, values, tags or {}, fmt, filed_at))
        curated = mx.load_breaks()
        for eid, slug, kind, ex in db.execute(
                """select ev.entity_id, ev.slug, ca.kind, ca.ex_date
                     from equity.corporate_action ca join core.event ev on ev.id = ca.event_id
                    where ev.entity_id = any(%s) and ev.status <> 'withdrawn' and ca.kind = any(%s)""",
                (ids, list(mx.BREAK_KINDS))).fetchall():
            f.companies[eid].breaks.append(mx.Break(kind, ex, slug))
        for c in f.companies.values():
            c.breaks += curated.get(c.slug, [])
        for r in db.execute(
                """select s.entity_id, s.id, s.call_event_id, s.metric, s.value, s.unit, s.period_kind, s.period_start,
                          s.period_end, s.basis, s.scope_text
                     from equity.stated_figure s
                     join core.ai_output o on o.id = s.ai_output_id and o.task = 'guidance_extraction' and o.status = 'published'
                    where s.entity_id = any(%s) and s.duplicate_of is null and s.metric = any(%s)
                    order by s.id""", (ids, list(sc.DEFINITION_METRICS))).fetchall():
            f.companies[r[0]].stated.append(sc.Stated(*r[1:]))
        for eid, c in f.companies.items():
            f.outcomes += sc.score_company(c, f.statements[eid], m, f.as_of)
        return f

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        scope = "and g.entity_id = any(%(ids)s)" if f.scope_ids is not None else ""
        p = {"ids": f.scope_ids}
        n = self._outcomes(tx, f, scope, p)
        n += self._forecasts(tx, f, scope, p, task_run_id)
        return n

    def _outcomes(self, tx, f: Fetched, scope: str, p: dict) -> int:
        have = {r[0]: r[1:] for r in tx.execute(
            f"""select o.guidance_id, o.target_key, o.is_first, o.revision, o.horizon_share, o.counted, o.outcome, o.relation,
                       o.actual, o.base, o.reason, o.inputs, o.map_version, o.rule_version
                  from equity.guidance_outcome o join equity.guidance g on g.id = o.guidance_id where true {scope}""",
            p).fetchall()}
        new, changed, want = [], [], set()
        mv = str(f.map.version)
        for o in f.outcomes:
            want.add(o.guidance_id)
            row = (o.target_key, o.is_first, o.revision, o.horizon_share, o.counted, o.outcome, o.relation, o.actual,
                   o.base, o.reason, _json(o.inputs), mv, sc.RULE_VERSION)
            old = have.get(o.guidance_id)
            if old is not None and tuple(old) == row:
                continue
            vals = (*row[:10], Jsonb(row[10]), *row[11:])
            if old is None:
                new.append((o.guidance_id, *vals))
            else:
                changed.append((*vals, o.guidance_id))
        stale = [(g,) for g in have if g not in want]
        with tx.cursor() as cur:
            if stale:
                cur.executemany("delete from equity.guidance_outcome where guidance_id = %s", stale)
            if new:
                cur.executemany("""insert into equity.guidance_outcome (guidance_id, target_key, is_first, revision,
                                       horizon_share, counted, outcome, relation, actual, base, reason, inputs, map_version,
                                       rule_version)
                                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", new)
            if changed:
                cur.executemany("""update equity.guidance_outcome set target_key = %s, is_first = %s, revision = %s,
                                       horizon_share = %s, counted = %s, outcome = %s, relation = %s, actual = %s, base = %s,
                                       reason = %s, inputs = %s, map_version = %s, rule_version = %s
                                    where guidance_id = %s""", changed)
        self.result.update(outcomes_inserted=len(new), outcomes_updated=len(changed), outcomes_deleted=len(stale))
        return len(new) + len(changed) + len(stale)

    def _forecasts(self, tx, f: Fetched, scope: str, p: dict, task_run_id) -> int:
        by_id = {s.id: s for ss in f.statements.values() for s in ss}
        wanted: dict[tuple, tuple] = {}            # (series key, vintage, start, end) -> the row
        self.collisions: list[str] = []
        for o in sorted(f.outcomes, key=lambda o: o.guidance_id):
            if not o.forecast_ok:
                continue
            s, r, c = by_id[o.guidance_id], f.rows[o.guidance_id], f.companies[by_id[o.guidance_id].entity_id]
            k = (f"guidance.{c.slug}.{s.metric}", s.vintage, s.start, s.end)
            if k in wanted:
                self.collisions.append(f"{c.slug} {s.metric} {s.start.isoformat()}–{s.end.isoformat()} "
                                       f"(guidance {o.guidance_id}; kept {wanted[k][0]})")
                continue
            scale = sc.CRORE if s.unit == "inr" else Decimal(1)
            mul = (lambda v, scale=scale: None if v is None else v * scale)
            wanted[k] = (o.guidance_id, sc.target_label(s.period_kind, s.start, s.end, c.fy_end_month),
                         mul(s.value) if s.kind == "point" else None, mul(s.low) if s.kind == "range" else None,
                         mul(s.high) if s.kind == "range" else None, r.document_id, r.char_start, r.char_end,
                         r.ai_output_id, s, r.metric_label)
        n = 0
        series = dict(tx.execute(
            f"""select s.key, s.id from core.series s where s.key like 'guidance.%%'
                   {'and s.entity_id = any(%(ids)s)' if f.scope_ids is not None else ''}""", p).fetchall())
        for key in sorted({k[0] for k in wanted} - series.keys()):
            gid = next(v[0] for k, v in wanted.items() if k[0] == key)
            s, label = by_id[gid], next(v[10] for k, v in wanted.items() if k[0] == key)
            name, market, currency = f.entities[s.entity_id]
            series[key] = tx.execute(
                """insert into core.series (key, name, kind, entity_id, source_id, source_code, unit, frequency, market,
                                            currency, is_published)
                   values (%s, %s, 'forecast', %s, %s, %s, %s, 'irregular', %s, %s, %s) returning id""",
                (key, f"{name}: {label or s.metric}, management guidance", s.entity_id, f.nse_id,
                 f"guidance_extraction.{s.metric}", SERIES_UNITS[s.unit], market, currency if s.unit == "inr" else None,
                 f.shown)).fetchone()[0]
            n += 1
        ids = sorted(series.values())
        have = {(r[0], r[1], r[2], r[3]): r[4:] for r in tx.execute(
            """select series_id, vintage_date, target_start, target_end, target_label, value, value_low, value_high,
                      document_id, char_start, char_end, ai_output_id
                 from core.forecast where series_id = any(%s)""", (ids,)).fetchall()}
        want_rows = {(series[k[0]], k[1], k[2], k[3]): v for k, v in wanted.items()}
        new, changed = [], []
        for k, v in want_rows.items():
            row = v[1:9]
            if k in have and tuple(have[k]) == tuple(row):
                continue
            (new if k not in have else changed).append((k, row))
        stale = [k for k in have if k not in want_rows]
        links = {v[0]: k[0] for k, v in want_rows.items()}
        old_links = dict(tx.execute(
            f"""select g.id, g.forecast_series_id from equity.guidance g
                 where g.forecast_series_id is not null {scope}""", p).fetchall())
        with tx.cursor() as cur:
            if stale:
                cur.executemany("""delete from core.forecast where series_id = %s and vintage_date = %s
                                      and target_start = %s and target_end = %s""", stale)
            if new:
                cur.executemany("""insert into core.forecast (series_id, vintage_date, target_start, target_end, target_label,
                                       value, value_low, value_high, method, document_id, char_start, char_end, ai_output_id,
                                       task_run_id)
                                   values (%s, %s, %s, %s, %s, %s, %s, %s, 'ai_extracted', %s, %s, %s, %s, %s)""",
                                [(*k, *row, task_run_id) for k, row in new])
            if changed:
                cur.executemany("""update core.forecast set target_label = %s, value = %s, value_low = %s, value_high = %s,
                                       document_id = %s, char_start = %s, char_end = %s, ai_output_id = %s, task_run_id = %s
                                    where series_id = %s and vintage_date = %s and target_start = %s and target_end = %s""",
                                [(*row, task_run_id, *k) for k, row in changed])
            unlink = [(g,) for g, sid in old_links.items() if links.get(g) != sid and g not in links]
            relink = [(sid, g) for g, sid in links.items() if old_links.get(g) != sid]
            if unlink:
                cur.executemany("update equity.guidance set forecast_series_id = null where id = %s", unlink)
            if relink:
                cur.executemany("update equity.guidance set forecast_series_id = %s where id = %s", relink)
        published = tx.execute(
            f"""update core.series s set is_published = %(shown)s
                 where s.key like 'guidance.%%' and s.is_published <> %(shown)s
                   {'and s.entity_id = any(%(ids)s)' if f.scope_ids is not None else ''}""",
            {**p, "shown": f.shown}).rowcount
        self.result.update(series_created=n, forecasts_inserted=len(new), forecasts_updated=len(changed),
                           forecasts_deleted=len(stale), links=len(unlink) + len(relink), series_publish_changed=published)
        return n + len(new) + len(changed) + len(stale) + len(unlink) + len(relink) + published

    # ------------------------------------------------------------------ checks
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        slug = {eid: c.slug for eid, c in f.companies.items()}
        ent = {s.id: s.entity_id for ss in f.statements.values() for s in ss}
        out = [self._inputs(tx, f, slug, ent)]
        forecasts, outcomes = tx.execute(
            """select (select count(*) from core.forecast x join equity.guidance g
                         on g.ai_output_id = x.ai_output_id and g.document_id = x.document_id
                        and g.char_start = x.char_start and g.char_end = x.char_end
                      where g.speaker_role <> 'management'),
                      (select count(*) from equity.guidance_outcome o join equity.guidance g on g.id = o.guidance_id
                        where g.speaker_role <> 'management')""").fetchone()
        out.append(Check("management_only", forecasts == 0 and outcomes == 0,
                         f"forecasts on unattributed statements: {forecasts}; outcomes on them: {outcomes}"))
        reasons = Counter(o.reason for o in f.outcomes if o.outcome == "not_scored")
        out.append(Check("not_scored", True, "targets not scored by reason: " + (
            ", ".join(f"{r} {n}" for r, n in reasons.most_common()) or "none")
            + (f"; {len(f.unscored)} statements of companies without a company row" if f.unscored else ""),
            blocking=False))
        failed: dict[tuple, dict] = {}
        for o in f.outcomes:
            if o.definition is not None:
                failed[(slug[ent[o.guidance_id]], o.definition["metric"])] = o.definition
        names = [f"{s} {mt} (" + (f"median {d['median']} {d['difference_unit']} over {d['n']}" if d["n"]
                                  else "no stated figure to check against") + ")" for (s, mt), d in sorted(failed.items())]
        out.append(Check("definition", not names, ("definition check failed: " + _names(names)) if names
                         else "no definition check failed", blocking=False))
        units = sorted({f"{slug[ent[o.guidance_id]]}: {o.unit_held}" for o in f.outcomes if o.unit_held})
        out.append(Check("units", not units, ("a filed fraction outside 0 to 1, held: " + _names(units)) if units
                         else "every filed fraction read is between 0 and 1", blocking=False))
        col = getattr(self, "collisions", [])
        out.append(Check("forecasts", not col, ("a second target of one metric, call and period, without a forecast row: "
                                                + _names(col)) if col else "every forecast-eligible target has its row",
                         blocking=False))
        out.append(self._scored(f, slug, ent))
        return out

    def _inputs(self, tx, f: Fetched, slug: dict, ent: dict) -> Check:
        bad = []
        ids = [o.guidance_id for o in f.outcomes]
        for gid, outcome, relation, actual, base, inputs in tx.execute(
                """select guidance_id, outcome, relation, actual, base, inputs from equity.guidance_outcome
                    where guidance_id = any(%s)""", (ids,)).fetchall():
            try:
                got = sc.recompute(inputs)
                ok = "statement" in inputs and got == (outcome, relation, actual, base)
            except (KeyError, TypeError, ValueError, ArithmeticError, mx.Held) as e:
                ok, got = False, repr(e)
            if not ok:
                bad.append(f"{slug.get(ent.get(gid), '?')} guidance {gid}")
        return Check("inputs", not bad, ("an outcome's inputs don't give it again: " + _names(bad)) if bad
                     else f"all {len(ids)} outcomes recompute from their inputs")

    @staticmethod
    def _scored(f: Fetched, slug: dict, ent: dict) -> Check:
        by_kind = Counter(f"{o.inputs['statement']['kind']} {o.outcome}" for o in f.outcomes)
        counted = [o for o in f.outcomes if o.counted and o.outcome in ("delivered", "missed")]
        per = Counter(slug[ent[o.guidance_id]] for o in counted)
        five = sorted(s for s, n in per.items() if n >= 5)
        pending = sum(1 for o in f.outcomes if o.counted and o.outcome == "pending")
        detail = (f"{len(f.outcomes)} outcomes; by outcome: "
                  + ", ".join(f"{k} {n}" for k, n in sorted(Counter(o.outcome for o in f.outcomes).items()))
                  + "; by type: " + (", ".join(f"{k} {n}" for k, n in sorted(by_kind.items())) or "none")
                  + f"; counted and scored {len(counted)} (counted, pending {pending}); companies with n >= 5: "
                  + (", ".join(five) or "none"))
        return Check("scored", True, detail, blocking=False)
