"""site.quality_summary: the site's own records, projected into core.quality_summary (issue #134, Accuracy 2; Plan 02 §4;
Brief §1 and §11). Counts only, from the database, by module and period (`30d`, `90d`, `all`), for the modules listed in
py/config/quality.yaml, so unpublished work never shows. No network: the work is one read and one rewrite.

Measures (the fixed list of migration 0021; the `site` module is never one of them):
- `ai_outputs` created in the period; `ai_published` (status `published`, or `superseded`: the batch task supersedes
  only an output that was published, so a superseded one was published once); `ai_rejected_<category>` for rejected
  outputs, by `ft/quality/reasons.py` (a rejected first attempt that was retried counts too: the page says "rejected by
  our checks", and the retry is its own output); `ai_held_review` (kept as a draft for review);
- `loads` (task runs that ran: not skipped, not running), `loads_rolled_back` (`checks_failed`), `loads_failed`;
- `freshness_on_time_share` (labelled `derived`): over each enabled task's completed UTC days in the period, from its
  first success on, the share of days that ended with its last success no older than its `overdue_after`; `n` = the
  task-days counted; no row while there are none;
- `drills_run`, `drills_passed` (closed records of ops.drill_run, by the drill's module in quality.yaml);
- `eval_gates_passed`, `eval_gates_pending`: the gates of py/config/eval/gates.yaml, passed when core.eval_summary holds a
  row for the gate's set and metric that meets its rule (the gates don't depend on the period: the same counts in each);
- `fields_hidden`, `fields_labelled`: core.field_policy rows by status (the same in each period). A field key belongs to
  the module of its prefix; `ai.<task>` to its AI task's module. A task shared by every module (`core`: doc_summary)
  belongs to none, and isn't counted.

An AI output's module is its task's, or, for a shared task, its target's: the document type's or the event type's.

Checks, B blocking, W warning: (B) every measure written is in the fixed list and no module outside quality.yaml; (B) no
value is negative and every share lies within 0 and 1; (B) one value per module, period and measure; (W) `other_reasons`:
rejected outputs whose reason no phrase table knows. The load replaces every row in one transaction; `as_of` is the run's
time. `due()` skips with "not set up" while core.quality_summary is missing, so a production `daily` between the merge
and Anand's `supabase db push` skips the task instead of failing.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register
from ft.quality import reasons

CONFIG = Path(__file__).resolve().parents[4] / "config"
PERIODS = {"30d": timedelta(days=30), "90d": timedelta(days=90), "all": None}
MEASURES = ("ai_outputs", "ai_published", "ai_rejected_citation", "ai_rejected_numbers", "ai_rejected_format",
            "ai_rejected_stopped", "ai_rejected_other", "ai_held_review", "loads", "loads_rolled_back", "loads_failed",
            "freshness_on_time_share", "drills_run", "drills_passed", "eval_gates_passed", "eval_gates_pending",
            "fields_hidden", "fields_labelled")
SHARES = ("freshness_on_time_share",)
LISTED = 10                                  # the warning lists at most this many distinct reasons


@dataclass
class Row:
    module: str
    period: str
    measure: str
    value: Decimal
    n: int | None = None
    label: str | None = None


@dataclass
class Gate:
    key: str
    module: str
    eval_set: str
    metric: str
    minimum: Decimal | None
    maximum: Decimal | None

    def met(self, value: Decimal) -> bool:
        return (self.minimum is None or value >= self.minimum) and (self.maximum is None or value < self.maximum)


@dataclass
class Settings:
    modules: tuple[str, ...]
    drills: dict[str, str]
    gates: list[Gate]


def load_settings(config_dir: Path = CONFIG) -> Settings:
    raw = yaml.safe_load((config_dir / "quality.yaml").read_text())
    modules = tuple(raw["modules"])
    drills = dict(raw.get("drills") or {})
    gates = []
    for g in yaml.safe_load((config_dir / "eval" / "gates.yaml").read_text())["gates"]:
        rule = g["rule"]
        if set(rule) - {"min", "max"} or not rule:
            raise ValueError(f"gate {g['key']}: a rule is min and/or max")
        gates.append(Gate(g["key"], g["module"], g["eval_set"], g["metric"],
                          Decimal(str(rule["min"])) if "min" in rule else None,
                          Decimal(str(rule["max"])) if "max" in rule else None))
    if bad := [m for m in [*drills.values(), *(g.module for g in gates)] if m not in modules]:
        raise ValueError(f"quality.yaml and gates.yaml name modules that aren't listed: {sorted(set(bad))}")
    return Settings(modules, drills, gates)


@dataclass
class Fetched:
    now: datetime
    settings: Settings
    rows: list[Row] = field(default_factory=list)
    unmatched: Counter = field(default_factory=Counter)      # reasons no phrase table knows (this run's text, not stored)


def _since(now: datetime, span: timedelta | None) -> datetime | None:
    return None if span is None else now - span


def _ai_rows(tx, f: Fetched) -> None:
    """ai_outputs, ai_published, ai_rejected_*, ai_held_review."""
    data = tx.execute(
        """select coalesce(nullif(t.module, 'core'), dt.module, et.module), o.status, o.status_reason, o.created_at
             from core.ai_output o
             join core.ai_task t on t.key = o.task
             left join core.document d on d.id = o.document_id
             left join core.doc_type dt on dt.key = d.doc_type
             left join core.event e on e.id = o.event_id
             left join core.event_type et on et.key = e.event_type
            where o.created_at <= %s""", (f.now,)).fetchall()
    for module in f.settings.modules:
        mine = [r for r in data if r[0] == module]
        for period, span in PERIODS.items():
            start = _since(f.now, span)
            inside = [r for r in mine if start is None or r[3] > start]
            count = Counter()
            for _, status, reason, _ in inside:
                count["ai_outputs"] += 1
                if status in ("published", "superseded"):
                    count["ai_published"] += 1
                cat, matched = reasons.categorise(status, reason)
                if cat == "held_review":
                    count["ai_held_review"] += 1
                elif cat is not None:
                    count["ai_rejected_" + cat] += 1
                if not matched and period == "all":
                    f.unmatched[(reason or "")[:160]] += 1
            for m in ("ai_outputs", "ai_published", *(f"ai_rejected_{c}" for c in reasons.CATEGORIES), "ai_held_review"):
                f.rows.append(Row(module, period, m, Decimal(count[m])))


def _load_rows(tx, f: Fetched) -> None:
    """loads, loads_rolled_back, loads_failed."""
    for module in f.settings.modules:
        for period, span in PERIODS.items():
            start = _since(f.now, span)
            counts = dict(tx.execute(
                """select r.status, count(*) from ops.task_run r join ops.task t on t.key = r.task_key
                    where t.module = %s and r.status in ('success', 'checks_failed', 'failed')
                      and r.started_at <= %s and (%s::timestamptz is null or r.started_at > %s)
                    group by r.status""", (module, f.now, start, start)).fetchall())
            f.rows += [Row(module, period, "loads", Decimal(sum(counts.values()))),
                       Row(module, period, "loads_rolled_back", Decimal(counts.get("checks_failed", 0))),
                       Row(module, period, "loads_failed", Decimal(counts.get("failed", 0)))]


def _freshness_rows(tx, f: Fetched) -> None:
    """freshness_on_time_share: derived, out of the task-days counted."""
    for module in f.settings.modules:
        for period, span in PERIODS.items():
            start = _since(f.now, span)
            on_time, total = tx.execute(
                """with today as (select date_trunc('day', %(now)s::timestamptz) as midnight),
                        first_ok as (select t.key, t.overdue_after, min(r.finished_at) as first_at
                                       from ops.task t join ops.task_run r on r.task_key = t.key and r.status = 'success'
                                      where t.module = %(module)s and t.enabled group by t.key, t.overdue_after),
                        days as (select f.key, f.overdue_after, g.d as day_start
                                   from first_ok f, today,
                                        lateral generate_series(
                                          greatest(date_trunc('day', f.first_at),
                                                   coalesce(date_trunc('day', %(start)s::timestamptz), date_trunc('day', f.first_at))),
                                          today.midnight - interval '1 day', interval '1 day') as g(d))
                   select count(*) filter (where last_ok is not null and day_start + interval '1 day' - last_ok <= overdue_after),
                          count(*)
                     from (select d.day_start, d.overdue_after,
                                  (select max(r.finished_at) from ops.task_run r
                                    where r.task_key = d.key and r.status = 'success'
                                      and r.finished_at < d.day_start + interval '1 day') as last_ok
                             from days d) x""",
                {"now": f.now, "module": module, "start": start}).fetchone()
            if total:
                f.rows.append(Row(module, period, "freshness_on_time_share", Decimal(on_time) / Decimal(total), total,
                                  "derived"))


def _drill_rows(tx, f: Fetched) -> None:
    for module in f.settings.modules:
        drills = [d for d, m in f.settings.drills.items() if m == module]
        for period, span in PERIODS.items():
            start = _since(f.now, span)
            run, passed = tx.execute(
                """select count(*), count(*) filter (where passed) from ops.drill_run
                    where drill = any(%s::text[]) and passed is not null and finished_at <= %s
                      and (%s::timestamptz is null or finished_at > %s)""", (drills, f.now, start, start)).fetchone()
            f.rows += [Row(module, period, "drills_run", Decimal(run)), Row(module, period, "drills_passed", Decimal(passed))]


def _gate_rows(tx, f: Fetched) -> None:
    summary: dict[tuple[str, str], list[Decimal]] = {}
    for eval_set, metric, value in tx.execute("select eval_set, metric, value from core.eval_summary").fetchall():
        summary.setdefault((eval_set, metric), []).append(value)
    for module in f.settings.modules:
        mine = [g for g in f.settings.gates if g.module == module]
        passed = sum(1 for g in mine if any(g.met(v) for v in summary.get((g.eval_set, g.metric), [])))
        for period in PERIODS:
            f.rows += [Row(module, period, "eval_gates_passed", Decimal(passed)),
                       Row(module, period, "eval_gates_pending", Decimal(len(mine) - passed))]


def _field_rows(tx, f: Fetched) -> None:
    ai_module = dict(tx.execute("select key, module from core.ai_task").fetchall())
    count: Counter = Counter()
    for key, status in tx.execute("select field_key, status from core.field_policy where status in ('hidden', 'labelled')"):
        prefix, _, rest = key.partition(".")
        module = ai_module.get(rest) if prefix == "ai" else prefix
        if module is not None and module != "core":
            count[(module, status)] += 1
    for module in f.settings.modules:
        for period in PERIODS:
            f.rows += [Row(module, period, "fields_hidden", Decimal(count[(module, "hidden")])),
                       Row(module, period, "fields_labelled", Decimal(count[(module, "labelled")]))]


def evaluate(f: Fetched, listed: set[str] | None = None) -> list[Check]:
    """The checks over the computed rows (a pure function of them)."""
    allowed = set(MEASURES)
    odd = sorted({r.measure for r in f.rows} - allowed)
    outside = sorted({r.module for r in f.rows} - set(f.settings.modules))
    bad_value = [f"{r.module}/{r.period}/{r.measure}" for r in f.rows
                 if r.value < 0 or (r.measure in SHARES and r.value > 1) or (r.n is not None and r.n < 0)]
    keys = Counter((r.module, r.period, r.measure) for r in f.rows)
    twice = sorted("/".join(k) for k, c in keys.items() if c > 1)
    return [
        Check("measures", not odd and not outside,
              f"measures outside the fixed list: {odd}; modules not in quality.yaml: {outside}" if odd or outside
              else f"{len(f.rows)} rows, every measure in the fixed list"),
        Check("values", not bad_value, f"negative or out-of-range: {bad_value[:LISTED]}" if bad_value else "no value negative, every share within 0 to 1"),
        Check("unique", not twice, f"written twice: {twice[:LISTED]}" if twice else "one value per module, period and measure"),
        Check("other_reasons", not f.unmatched,
              ("rejected outputs whose reason no phrase table knows (map the phrase in ft/quality/reasons.py's tables): "
               + " | ".join(f"{n} x {r!r}" for r, n in f.unmatched.most_common(LISTED))) if f.unmatched
              else "every rejection reason is mapped", blocking=False),
    ]


@register
class QualitySummary(Task):
    key = "site.quality_summary"

    def due(self, db, workflow: str, now: datetime | None = None) -> Due:
        if not db.execute("select to_regclass('core.quality_summary') is not null and to_regclass('ops.drill_run') is not null"
                          ).fetchone()[0]:
            return Due(False, "not set up: its migration isn't pushed to this project (core.quality_summary)")
        return super().due(db, workflow, now)

    def fetch(self, ctx: Context) -> Fetched:
        return Fetched(ctx.now, load_settings())

    def load(self, tx, f: Fetched, task_run_id) -> int:
        for step in (_ai_rows, _load_rows, _freshness_rows, _drill_rows, _gate_rows, _field_rows):
            step(tx, f)
        blocking = [c for c in evaluate(f) if c.blocking and not c.passed]
        if blocking:
            return 0                                   # the checks stop the run next: nothing is replaced
        tx.execute("delete from core.quality_summary")
        with tx.cursor() as cur:
            cur.executemany(
                "insert into core.quality_summary (module, period, measure, value, n, label, as_of) values (%s,%s,%s,%s,%s,%s,%s)",
                [(r.module, r.period, r.measure, r.value, r.n, r.label, f.now) for r in f.rows])
        return len(f.rows)

    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        return evaluate(f)
