"""cmdbar.examples: the command bar's example chips, precomputed (Spec 07 §4 "Examples", §9 step 4; Architecture 03 §1.8; K-09, #588).

**Ships disabled** (`enabled: false` in config/tasks.yaml): nothing runs on production until go-live, when enabling it is Anand's
step on K-11's list (#590), with production's `sql_reader` connection as the GitHub secret `SQL_READER_DATABASE_URL`. The dev
trial while it is disabled is this module's `main` (`cmdbar-gates.yml`'s `examples`), dev only.

The chips are config/cmdbar/examples.yaml. Each run, for each chip in display order:
- **unchanged:** its stored answer is `answered` and its `data_version` hasn't moved: nothing is run or asked. The version is the
  route cache's (`ft.cmdbar.cache.answer_version`): the newest last success of the tasks feeding the views a data answer read,
  or of the policy tasks for a document answer.
- **rows unchanged:** a data answer whose version moved: its stored query (the re-printed one that ran) goes through the guard
  again and runs as `sql_reader` (read-only, 3 s, rolled back), with no model call. The same columns and rows: the answer stands
  as checked (its summary was grounded in those rows), and only `data_version` moves.
- **asked** otherwise (a new chip, a document answer whose version moved, a data answer whose rows moved or whose query no longer
  runs, a stored answer that isn't `answered`): through the route's own steps 4 and 5 (`ft.cmdbar.route.answer`: the router,
  then the data or document route, the code the gates measure), with no switch, limits, cache or log, which are for visitors.
  - An answer the route wouldn't cache (a failure: the database down, a refused call, AI unavailable) changes nothing: the
    stored answer stays as it was and the chip is asked again next run.
  - A data answer replaces the stored one; it stays checked only if its columns, rows and summary equal the checked answer's.
  - A document answer that cites the same documents leaves the checked answer standing (its quotes are verified spans of stored
    text, which never changes once chunked: `CLAUDE.md` §3); citing other documents replaces it, unchecked.
  - An answer that isn't `answered` (can't answer, not found) is stored with no `data_version`, so it is asked again next run.
- **When an answer changes, `verified` goes false**: the admin checks it again before a visitor sees it (`api.cmdbar_examples`
  returns checked rows only). A chip taken out of the file is set unchecked too (`job_writer` can't delete it).

**Spend:** before each ask the run stops asking (`capped`) if its spend plus one chip's worst case (`WORST_CHIP_USD`) could pass
`RUN_CAP_USD`, or the month's AI spend plus that could reach `ai_monthly_budget_usd`. The run's cost goes on
`ops.task_run.ai_cost_usd`, which the monthly switch sums (N8). No Anthropic key: nothing is asked (`ai_off`).

**Checks:** `reader` (blocking) that `SQL_READER_DATABASE_URL` is set; `chips` (blocking) that every chip in the file has its row
and no chip outside it is checked; `answers` (informational) the outcome counts; `ai_spend` (informational) the run's cost.
What it prints (the run log is public: `CLAUDE.md` §4) is each chip's order, route, state, outcome and row count, never a row,
a summary or a figure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

from ft.ai import batch, sqlguard
from ft.ai.client import Client
from ft.cmdbar import MAX_QUESTION_CHARS, cache
from ft.cmdbar import route as route_mod
from ft.cmdbar import sql as sql_mod
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

KEY = "cmdbar.examples"
EXAMPLES = Path(__file__).resolve().parents[4] / "config" / "cmdbar" / "examples.yaml"
MODULES = ("policy", "equity")
MIN_CHIPS, MAX_CHIPS = 4, 6                       # Spec 07 §7: four to six example chips
RUN_CAP_USD = Decimal("0.40")                     # one run's ceiling: six chips at their worst
WORST_CHIP_USD = Decimal("0.06")                  # router, SQL, Sonnet's one retry and the summary; or a document answer
CLIENT_TIMEOUT_S, CLIENT_RETRIES, CLIENT_MAX_WAIT_S = 60.0, 2, 30.0
READER_CONNECT_TIMEOUT_S = 10
OUTCOMES = ("unchanged", "rows_unchanged", "kept", "changed", "new", "failed", "ai_off", "capped")


class ExamplesError(ValueError):
    """config/cmdbar/examples.yaml isn't what the task needs."""


@dataclass(frozen=True)
class Chip:
    question: str
    module: str
    display_order: int


def load_examples(path: Path | str = EXAMPLES) -> list[Chip]:
    """The chips, in display order. ExamplesError if the file breaks a rule (four to six chips, unique questions and orders)."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    items = doc.get("examples") if isinstance(doc, dict) else None
    if not isinstance(items, list) or not MIN_CHIPS <= len(items) <= MAX_CHIPS:
        raise ExamplesError(f"examples: a list of {MIN_CHIPS} to {MAX_CHIPS} chips")
    chips = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"question", "module", "display_order"}:
            raise ExamplesError(f"chip {i + 1}: exactly question, module and display_order")
        q, module, order = item["question"], item["module"], item["display_order"]
        if not isinstance(q, str) or not q.strip() or q != " ".join(q.split()) or len(q) > MAX_QUESTION_CHARS:
            raise ExamplesError(f"chip {i + 1}: a one-line question of at most {MAX_QUESTION_CHARS} characters")
        if module not in MODULES:
            raise ExamplesError(f"chip {i + 1}: module must be one of {', '.join(MODULES)}")
        if isinstance(order, bool) or not isinstance(order, int) or not 1 <= order <= 32767:
            raise ExamplesError(f"chip {i + 1}: display_order must be a whole number from 1")
        chips.append(Chip(q, module, order))
    if len({c.question for c in chips}) != len(chips) or len({cache.normalise(c.question) for c in chips}) != len(chips):
        raise ExamplesError("two chips ask the same question")
    if len({c.display_order for c in chips}) != len(chips):
        raise ExamplesError("two chips share a display_order")
    return sorted(chips, key=lambda c: c.display_order)


# ---------------------------------------------------------------------------------------------- comparing answers
def answered(answer) -> bool:
    return isinstance(answer, dict) and answer.get("state") == "answered"


def version_of(answer: dict, freshness) -> str | None:
    """A stored answer's `data_version`: the route cache's for an answer, none otherwise (asked again next run)."""
    return cache.answer_version(answer, freshness) if answered(answer) else None


def cited_documents(answer) -> tuple:
    return tuple(sorted({d.get("document_id") for d in (answer or {}).get("documents") or [] if isinstance(d, dict)},
                        key=repr))


def same_data(old, new) -> bool:
    """Two data answers state the same thing: the same columns, rows and summary (the SQL that produced them may differ)."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    return all(json.dumps(old.get(k), sort_keys=True) == json.dumps(new.get(k), sort_keys=True)
               for k in ("route", "state", "columns", "rows", "summary"))


def same_documents(old, new) -> bool:
    """Two document answers rest on the same documents (both answered, citing the same set)."""
    return answered(old) and answered(new) and old.get("route") == new.get("route") == "document" \
        and bool(cited_documents(old)) and cited_documents(old) == cited_documents(new)


# ---------------------------------------------------------------------------------------------- the run
@dataclass
class Stored:
    question: str
    answer: dict | None
    data_version: str | None
    verified: bool


@dataclass
class Plan:
    chip: Chip
    outcome: str
    write: bool = False                  # the row's answer, data_version and verified are set from this plan
    answer: dict | None = None
    data_version: str | None = None
    reset: bool = False                  # set verified false (otherwise the row's own value stands, as the admin left it)
    cost: Decimal = Decimal(0)

    def line(self) -> str:
        a = self.answer if self.write else None
        bits = [f"chip {self.chip.display_order}", self.outcome]
        if a:
            bits += [str(a.get("route")), str(a.get("state"))]
            if a.get("route") == "data" and isinstance(a.get("rows"), list):
                bits.append(f"{len(a['rows'])} rows")
            if a.get("route") == "document":
                bits.append(f"{len(a.get('documents') or [])} documents")
        return "  " + ", ".join(bits)


@dataclass
class Run:
    chips: list[Chip]
    plans: list[Plan] = field(default_factory=list)
    reader_set: bool = True
    cost: Decimal = Decimal(0)


class _Pages:
    """The route's store as the precompute needs it (`route.answer`): the freshness it read, and no page lookups (a chip is a
    data or document question, so a page answer is a failure: not stored, asked again next run)."""

    def __init__(self, freshness):
        self.fresh = freshness

    def freshness(self):
        return dict(self.fresh)

    def page(self, slug):
        raise route_mod.StoreError("a chip is a data or document question")

    def close(self) -> None:
        pass


def deps(env: Mapping[str, str], store: _Pages) -> route_mod.Deps:
    """The route's dependencies for a job: the jobs' longer waits, the same prompt, guard, reader and embedder."""
    return route_mod.Deps(
        env=env, store=lambda _env: store,
        client=lambda e: Client(e["ANTHROPIC_API_KEY"].strip(), timeout=CLIENT_TIMEOUT_S, retries=CLIENT_RETRIES,
                                max_wait=CLIENT_MAX_WAIT_S),
        reader=lambda e: sql_mod.Reader.connect(e["SQL_READER_DATABASE_URL"].strip(), connect_timeout=READER_CONNECT_TIMEOUT_S),
        prompt=route_mod.cached_prompt)


def rerun(reader: sql_mod.Reader, answer: dict) -> bool:
    """A stored data answer's query, through the guard again and run as `sql_reader`: True if it returns the same columns and
    rows (no model call). Any failure is False (the chip is then asked)."""
    sql = answer.get("sql")
    if answer.get("route") != "data" or not isinstance(sql, str) or not sql:
        return False
    try:
        got = reader.run(sqlguard.guard(sql))
    except (sqlguard.Rejected, sql_mod.QueryError, sql_mod.ReaderDown):
        return False
    columns, rows = route_mod.table(got.columns, got.rows)
    return json.dumps([columns, rows]) == json.dumps([answer.get("columns"), answer.get("rows")])


def plan_chip(chip: Chip, old: Stored | None, freshness, *, ask, reread) -> Plan:
    """What one chip needs this run. `ask(question)` returns the route's (final, cacheable, log row), or the outcome `ai_off`
    or `capped` when nothing may be sent; `reread(answer)` re-runs a stored data answer's query."""
    prev = old.answer if old else None
    if answered(prev) and old.data_version is not None and old.data_version == version_of(prev, freshness):
        return Plan(chip, "unchanged")
    if answered(prev) and prev.get("route") == "data" and reread(prev):
        return Plan(chip, "rows_unchanged", write=True, answer=prev, data_version=version_of(prev, freshness))
    got = ask(chip.question)
    if isinstance(got, str):                             # ai_off or capped: nothing sent
        return Plan(chip, got)
    out, keep, row = got
    cost = row.cost_usd
    if not keep or out.get("state") == "paused":         # a failure: the stored answer stands, asked again next run
        return Plan(chip, "failed", cost=cost)
    if prev is None:
        return Plan(chip, "new", write=True, answer=out, data_version=version_of(out, freshness), reset=True, cost=cost)
    if same_documents(prev, out):
        return Plan(chip, "kept", write=True, answer=prev, data_version=version_of(prev, freshness), cost=cost)
    if out.get("route") != "document" and same_data(prev, out):
        return Plan(chip, "kept", write=True, answer=out, data_version=version_of(out, freshness), cost=cost)
    return Plan(chip, "changed", write=True, answer=out, data_version=version_of(out, freshness), reset=True, cost=cost)


@register
class CmdbarExamples(Task):
    key = KEY

    def __init__(self, env: Mapping[str, str] | None = None, path: Path | str | None = None):
        self.env = os.environ if env is None else env
        self.path = path or EXAMPLES
        self.ask_fn = route_mod.answer                   # the tests swap these for fakes
        self.reader_fn = lambda e: sql_mod.Reader.connect(e["SQL_READER_DATABASE_URL"].strip(),
                                                          connect_timeout=READER_CONNECT_TIMEOUT_S)

    def fetch(self, ctx: Context) -> Run:
        run = Run(load_examples(self.path))
        if not (self.env.get("SQL_READER_DATABASE_URL") or "").strip():
            run.reader_set = False
            print("  SQL_READER_DATABASE_URL is not set: no chip is run or asked")
            return run
        db = ctx.fetcher.db
        stored = {q: Stored(q, a if isinstance(a, dict) else None, v, bool(ok)) for q, a, v, ok in db.execute(
            "select question, answer, data_version, verified from ops.cmdbar_example").fetchall()}
        freshness = {k: t for k, t in db.execute("select task_key, last_success_at from core.task_freshness").fetchall()}
        budget, month = batch.monthly_budget(db), batch.month_spend(db)
        key_set = bool((self.env.get("ANTHROPIC_API_KEY") or "").strip())
        store = _Pages(freshness)
        route_deps = deps(self.env, store)

        def ask(question: str):
            if not key_set:
                return "ai_off"
            if run.cost + WORST_CHIP_USD > RUN_CAP_USD or month + run.cost + WORST_CHIP_USD >= budget:
                return "capped"
            got = self.ask_fn(question, route_deps, store)
            run.cost += got[2].cost_usd
            return got

        reader = None

        def reread(answer: dict) -> bool:
            nonlocal reader
            try:
                if reader is None:
                    reader = self.reader_fn(self.env)
                return rerun(reader, answer)
            except sql_mod.ReaderDown:
                return False

        try:
            for chip in run.chips:
                p = plan_chip(chip, stored.get(chip.question), freshness, ask=ask, reread=reread)
                run.plans.append(p)
                print(p.line())
        finally:
            if reader is not None:
                reader.close()
        return run

    def load(self, tx, r: Run, task_run_id) -> int:
        n = 0
        for chip in r.chips:                             # every chip has its row, its module and order kept current
            n += tx.execute("""insert into ops.cmdbar_example (question, module, display_order) values (%s, %s, %s)
                               on conflict (question) do update set module = excluded.module,
                                                                  display_order = excluded.display_order
                                where ops.cmdbar_example.module is distinct from excluded.module
                                   or ops.cmdbar_example.display_order is distinct from excluded.display_order""",
                            (chip.question, chip.module, chip.display_order)).rowcount
        for p in r.plans:
            if not p.write:
                continue
            n += tx.execute("""update ops.cmdbar_example
                                  set answer = %(answer)s::jsonb, data_version = %(version)s::text,
                                      verified = case when %(reset)s then false else verified end, updated_at = now()
                                where question = %(q)s""",
                            {"answer": json.dumps(p.answer, ensure_ascii=False), "version": p.data_version,
                             "reset": p.reset, "q": p.chip.question}).rowcount
        n += tx.execute("""update ops.cmdbar_example set verified = false, updated_at = now()
                            where verified and question <> all(%s)""", ([c.question for c in r.chips],)).rowcount
        tx.execute("update ops.task_run set ai_cost_usd = %s where id = %s",
                   (r.cost.quantize(Decimal("0.000001")), task_run_id))
        return n

    def checks(self, tx, r: Run, task_run_id) -> list[Check]:
        questions = [c.question for c in r.chips]
        have = tx.execute("select count(*) from ops.cmdbar_example where question = any(%s)", (questions,)).fetchone()[0]
        stray = tx.execute("select count(*) from ops.cmdbar_example where verified and question <> all(%s)",
                           (questions,)).fetchone()[0]
        counts = Counter(p.outcome for p in r.plans)
        trouble = counts["failed"] + counts["capped"]
        return [Check("reader", r.reader_set, "SQL_READER_DATABASE_URL is not set" if not r.reader_set else "set"),
                Check("chips", have == len(questions) and stray == 0,
                      f"{have} of {len(questions)} chips stored, {stray} checked chips outside the file"),
                Check("answers", trouble == 0, json.dumps({k: counts[k] for k in OUTCOMES if counts[k]}), blocking=False),
                Check("ai_spend", True, f"{r.cost.quantize(Decimal('0.000001'))} USD", blocking=False)]


# ---------------------------------------------------------------------------------------------- the dev trial
def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    """The task on **dev** while it is disabled (K-09's dev run; `cmdbar-gates.yml`'s `examples`): one `refresh` job run with
    this task alone, forced, as `job_writer`. There is no production target: the task reaches production only through `daily`
    once Anand enables it at go-live."""
    from ft.jobs import config
    from ft.jobs.runner import JobRun, connect

    p = argparse.ArgumentParser(prog="python -m ft.jobs.tasks.cmdbar_examples", description=main.__doc__)
    p.add_argument("--target", choices=["dev"], required=True)
    p.add_argument("--runner", choices=["local", "github"], default="local")
    p.add_argument("--github-run-id", type=int)
    p.add_argument("--git-sha")
    args = p.parse_args(argv)
    env = os.environ if env is None else env
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (the workflow reads DEV_JOB_DATABASE_URL)")
    configs = config.load()
    cfg = configs[KEY]
    task = CmdbarExamples(env)
    task.workflows, task.rules, task.run_after = cfg.workflows, dict(cfg.rules), cfg.run_after
    task.min_interval, task.uses_nse, task.label = cfg.min_interval, cfg.uses_nse, cfg.description
    with connect(dsn, autocommit=True, role="sync") as c:
        config.sync(c, configs)
    print(f"{KEY}: dev trial ({'enabled' if cfg.enabled else 'disabled in config/tasks.yaml'}), runner {args.runner}")
    with JobRun(dsn, "refresh", trigger="cli", runner=args.runner, github_run_id=args.github_run_id, git_sha=args.git_sha,
                target="dev") as job:
        job.run_pass([task], only=KEY, force=True)
        return job.finish()


if __name__ == "__main__":
    sys.exit(main())
