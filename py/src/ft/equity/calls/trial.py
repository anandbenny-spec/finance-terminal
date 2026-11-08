"""Extraction trial (#178, sitting 2; Anand's 8 Nov 2026, 12:18 IST go on the recall fix, trial first): the same stored calls
asked of the extraction under several settings at once ("arms": a model, a prompt version, an effort, a token cap), to choose
the fix for the recall gate's 47% before anything is re-extracted. A development tool: dev only, manual only.

What it does and does not touch:
- **Reads** the stored calls (text, turns, fiscal-year end) as `equity.calls_ai` does, and builds each request with
  `ft.ai.guidance_extraction.guidance_request`, so an arm differs from production in its model, prompt version, effort and
  token cap only.
- **Sends one Batch API batch** (half price) holding every arm × call, after printing the price. `--dry-run` (the default) sends
  nothing: it prints each arm's estimated input tokens and the worst-case cost (every request using its whole token cap), and
  refuses a batch whose worst case is over `--max-cost`. The batch is collected in the same run (polled up to `--wait-minutes`),
  or later with `--collect <batch id>` given the same events and arms.
- **Writes** `ops.eval_run` (one row per arm, `passed` false: a trial isn't a gate) and `ops.eval_result` (one row per kept
  target: its fields and its span, **no quote and no transcript text**). It writes nothing to `core.*`, `equity.*` or
  `ops.ai_request`: published outputs stay as they are, and nothing here can supersede one. Its spend is in the run's metrics and
  is **not** in `ops.task_run.ai_cost_usd` (the monthly switch's sum), so the issue records it.
- **Prints** counts and ids as annotations (Claude reads results from annotations): never a quote, never a transcript word.
  Quotes for the spans are read in the session through the read-only dev connector, into the chat only (Spec 02B §13.11 L12).

An arm is `model:prompt:effort:max_tokens`, for example `sonnet:v1:medium:16000` or `haiku:v2:low:24000`. Any part after the model
may be left out (`haiku:v2`): the prompt version defaults to `v1`, the effort and the cap to the task's.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from ft.ai import guidance_extraction as ge
from ft.ai.client import HAIKU, MILLION, SONNET, Client, Request, TaskSpec, load_prompt, load_schema, price_entry

MODELS = {"haiku": HAIKU, "sonnet": SONNET}
EFFORTS = ("low", "medium", "high")
MAX_TOKENS_LIMIT = 64_000                  # a typo guard, not the API's limit
DEFAULT_MAX_COST = Decimal("0.70")
DEFAULT_WAIT_MINUTES = 45
POLL_SECONDS = 30
EVAL_SET = "2b15_recall_trial"
SET_VERSION = "1"
CUSTOM_ID = re.compile(r"trial-a(\d+)-e(\d+)")


# ---------------------------------------------------------------------------------------------- arms and requests
@dataclass(frozen=True)
class Arm:
    model: str
    prompt: str = "v1"
    effort: str | None = None             # None: the task's
    max_tokens: int | None = None

    @property
    def name(self) -> str:
        short = next((k for k, v in MODELS.items() if v == self.model), self.model)
        return ":".join([short, self.prompt, self.effort or "task", str(self.max_tokens or "task")])


def parse_arm(text: str) -> Arm:
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 4 or parts[0] not in MODELS:
        raise ValueError(f"arm {text!r}: expected model[:prompt[:effort[:max_tokens]]], the model one of {sorted(MODELS)}")
    prompt = parts[1] if len(parts) > 1 and parts[1] else "v1"
    if not re.fullmatch(r"v\d+", prompt):
        raise ValueError(f"arm {text!r}: the prompt version must look like v2")
    effort = parts[2] if len(parts) > 2 and parts[2] else None
    if effort is not None and effort not in EFFORTS:
        raise ValueError(f"arm {text!r}: the effort must be one of {EFFORTS}")
    cap = None
    if len(parts) > 3 and parts[3]:
        if not parts[3].isdigit() or not 1_000 <= int(parts[3]) <= MAX_TOKENS_LIMIT:
            raise ValueError(f"arm {text!r}: max_tokens must be a whole number from 1,000 to {MAX_TOKENS_LIMIT:,}")
        cap = int(parts[3])
    return Arm(MODELS[parts[0]], prompt, effort, cap)


def parse_arms(text: str) -> list[Arm]:
    arms = [parse_arm(a) for a in re.split(r"[\s,]+", text.strip()) if a]
    if not arms:
        raise ValueError("no arms")
    if len({a.name for a in arms}) != len(arms):
        raise ValueError("the same arm twice")
    return arms


@dataclass(frozen=True)
class TrialRequest(Request):
    """A guidance request with its effort and token cap set by the arm, otherwise the task's."""
    effort: str | None = None
    max_tokens: int | None = None

    @property
    def spec(self) -> TaskSpec:
        base = Request.spec.fget(self)
        return replace(base, effort=self.effort or base.effort, max_tokens=self.max_tokens or base.max_tokens)


def build_request(call, arm: Arm) -> Request:
    """The production request for `call` (its prompt version and schema from `arm.prompt`) with the arm's model, effort and cap."""
    prompt = load_prompt(ge.TASK, arm.prompt)
    schema = load_schema(ge.TASK, arm.prompt)
    base = ge.guidance_request(call, prompt, schema)
    return TrialRequest(base.task, base.prompt, base.sources, base.instruction, model=arm.model, schema=base.schema,
                        effort=arm.effort, max_tokens=arm.max_tokens)


def worst_case_cost(req: Request, *, on=None) -> Decimal:
    """The batch price if the request used its whole token cap: the estimated input at the batch rate plus `max_tokens` of
    output (thinking included) at the batch rate."""
    prices, factor, _ = price_entry(req.model_id, on or datetime.now(UTC).date())
    p = prices["standard"]
    return ((Decimal(req.estimated_tokens()) * Decimal(str(p["input"])) + Decimal(req.spec.max_tokens) * Decimal(str(p["output"])))
            / MILLION * factor)


def custom_id(arm_no: int, event_id: int) -> str:
    return f"trial-a{arm_no}-e{event_id}"


# ---------------------------------------------------------------------------------------------- results
@dataclass
class CallResult:
    arm_no: int
    event_id: int
    document_id: int
    status: str                            # checked | stopped | errored | canceled | expired | not_json
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal(0)
    problems: tuple[str, ...] = ()
    targets: tuple[dict, ...] = ()         # kept targets, without their quotes
    stated: int = 0
    drops: tuple[tuple[str, int], ...] = ()

    @property
    def management(self) -> int:
        return sum(1 for t in self.targets if t["speaker_role"] == "management" and t["duplicate_of"] is None)

    def summary(self) -> dict:
        return {"event": self.event_id, "status": self.status, "stop": self.stop_reason, "in": self.input_tokens,
                "out": self.output_tokens, "cost": f"{self.cost:.4f}", "targets": len(self.targets),
                "management": self.management, "stated": self.stated, "dropped": dict(self.drops),
                **({"problems": list(self.problems)} if self.problems else {})}


def target_row(t: dict) -> dict:
    """A kept target as stored for the trial: every field except the quote (the span says where it is; text is read in the
    session through the read-only connector, never stored here)."""
    return {k: v for k, v in t.items() if k != "quote"}


def judge(result, work, arm_no: int):
    """One batch result as a `CallResult`: checked like production checks a reply (a reply that didn't end its turn is a failure,
    as in the wrapper)."""
    base = {"arm_no": arm_no, "event_id": work.event_id, "document_id": work.document_id}
    if result.kind != "succeeded" or result.reply is None:
        return CallResult(**base, status=result.kind if result.kind != "succeeded" else "errored",
                          problems=(result.error,) if result.error else ())
    reply = result.reply
    usage = {"input_tokens": reply.usage.prompt_tokens, "output_tokens": reply.usage.output, "cost": reply.cost_usd,
             "stop_reason": reply.stop_reason}
    if reply.stop_reason != "end_turn":
        return CallResult(**base, status="stopped", **usage)
    answer = ge.answer_of(reply)
    checked = ge.check(answer, work.call, work.metrics)
    if not checked.ok:
        return CallResult(**base, status="not_json", problems=tuple(checked.problems), **usage)
    return CallResult(**base, status="checked", targets=tuple(target_row(t) for t in checked.targets),
                      stated=len(checked.stated), drops=tuple(sorted(checked.drops.items())), **usage)


def arm_metrics(arm: Arm, results: list[CallResult], batch_id: str) -> dict:
    return {"arm": arm.name, "effort": arm.effort, "max_tokens": arm.max_tokens, "batch_id": batch_id,
            "cost_usd": f"{sum((r.cost for r in results), Decimal(0)):.6f}", "calls": [r.summary() for r in results],
            "judge": "none: a development trial of the extraction, not a gate"}


# ---------------------------------------------------------------------------------------------- database
def read_works(conn, event_ids: list[int]):
    """The stored calls for `event_ids` as extraction `Work`, exactly as `equity.calls_ai` builds them (every call that could be
    asked for, summaries aside). A call that can't be extracted is an error here: a trial names its calls."""
    from ft.jobs.tasks.equity_calls_ai import EquityCallsAi

    rows = conn.execute("""select event_id, transcript_document_id from equity.earnings_call
                            where event_id = any(%s)""", (event_ids,)).fetchall()
    docs = {eid: did for eid, did in rows}
    missing = [e for e in event_ids if docs.get(e) is None]
    if missing:
        raise SystemExit(f"no stored transcript for call events {missing}")
    works = [w for w in EquityCallsAi()._calls(conn, doc_ids=sorted(docs.values()), lacking=False) if w.task == ge.TASK]
    by_event = {w.event_id: w for w in works}
    absent = [e for e in event_ids if e not in by_event]
    if absent:
        raise SystemExit(f"call events {absent} can't be extracted (not stored, not published, no turns or no date)")
    return [by_event[e] for e in event_ids]


def already_recorded(conn, batch_id: str) -> bool:
    return conn.execute("select 1 from ops.eval_run where eval_set = %s and metrics->>'batch_id' = %s limit 1",
                        (EVAL_SET, batch_id)).fetchone() is not None


def record(conn, arms: list[Arm], results: list[CallResult], batch_id: str, notes: str) -> list[int]:
    """One `ops.eval_run` per arm and one `ops.eval_result` per kept target, in one transaction. Idempotent per batch."""
    from psycopg.types.json import Jsonb

    if already_recorded(conn, batch_id):
        return []
    ids = []
    with conn.transaction():
        for no, arm in enumerate(arms):
            mine = [r for r in results if r.arm_no == no]
            rows = [(f"{r.event_id}:{i}", t) for r in mine for i, t in enumerate(r.targets)]
            run_id = conn.execute(
                """insert into ops.eval_run (eval_set, set_version, model, prompt_version, n_items, metrics, passed, notes)
                   values (%s, %s, %s, %s, %s, %s, false, %s) returning id""",
                (EVAL_SET, SET_VERSION, arm.model, arm.prompt, len(rows), Jsonb(arm_metrics(arm, mine, batch_id)), notes)
            ).fetchone()[0]
            for key, t in rows:
                conn.execute("""insert into ops.eval_result (eval_run_id, item_key, expected, predicted)
                                values (%s, %s, '{}'::jsonb, %s)""", (run_id, key, Jsonb(t)))
            ids.append(run_id)
    return ids


# ---------------------------------------------------------------------------------------------- the CLI
def annotation(title: str, text: str) -> str:
    esc = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::notice title={title}::{esc}"


def parse_events(text: str) -> list[int]:
    ids = [int(x) for x in re.split(r"[\s,]+", text.strip()) if x]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("events: a list of distinct call event ids")
    return ids


def main(argv: list[str] | None = None, *, env=None, out=print, client=None, sleep=time.sleep) -> int:
    p = argparse.ArgumentParser(prog="python -m ft.equity.calls.trial", description=__doc__.split("\n\n")[0])
    p.add_argument("--events", required=True, help="call event ids, comma or space separated")
    p.add_argument("--arms", required=True, help="arms, e.g. 'sonnet:v1:medium:16000 haiku:v2:medium:24000'")
    p.add_argument("--max-cost", type=Decimal, default=DEFAULT_MAX_COST, help="refuse a batch whose worst-case cost is over this")
    p.add_argument("--wait-minutes", type=int, default=DEFAULT_WAIT_MINUTES)
    p.add_argument("--send", action="store_true", help="send the batch; without it nothing is sent (the price only)")
    p.add_argument("--collect", default="", help="a batch id: read its results (same --events and --arms) instead of sending")
    p.add_argument("--target", choices=("dev",), default="dev", help="logged; the workflow reads dev's credentials only")
    args = p.parse_args(argv)
    env = os.environ if env is None else env
    try:
        arms, events = parse_arms(args.arms), parse_events(args.events)
    except ValueError as e:
        p.error(str(e))
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (the workflow reads the DEV_ secret)")
    if (args.send or args.collect) and not (env.get("ANTHROPIC_API_KEY") or "").strip() and client is None:
        p.error("ANTHROPIC_API_KEY is not set")
    from ft.jobs.runner import connect

    with connect(dsn, autocommit=True, role="extraction-trial") as conn:
        works = read_works(conn, events)
        items = [(no, w, build_request(w.call, arm)) for no, arm in enumerate(arms) for w in works]
        per_arm = {}
        for no, arm in enumerate(arms):
            mine = [r for n, _, r in items if n == no]
            per_arm[arm.name] = {"requests": len(mine), "estimated_input_tokens": sum(r.estimated_tokens() for r in mine),
                                 "worst_case_usd": f"{sum(worst_case_cost(r) for r in mine):.4f}"}
        worst = sum(worst_case_cost(r) for _, _, r in items)
        out(annotation("trial price", json.dumps({"events": events, "requests": len(items), "arms": per_arm,
                                                  "worst_case_usd": f"{worst:.4f}", "max_cost_usd": str(args.max_cost),
                                                  "batch_price": "half the real-time price (ai_prices.yaml)"},
                                                 separators=(",", ":"))))
        if worst > args.max_cost:
            out(f"::error::worst-case cost {worst:.4f} USD is over --max-cost {args.max_cost}; nothing sent")
            return 1
        if not (args.send or args.collect):
            out(annotation("trial dry run", "nothing sent; run again with send on, or --collect <batch id>"))
            return 0
        claude = client or Client(env["ANTHROPIC_API_KEY"].strip())
        model_of = {custom_id(no, w.event_id): arm.model for no, arm in enumerate(arms) for w in works}
        if args.collect:
            info = claude.batch(args.collect)
        else:
            info = claude.create_batch([(custom_id(no, w.event_id), req) for no, w, req in items])
            out(annotation("trial batch", json.dumps({"batch_id": info.id, "requests": len(items)}, separators=(",", ":"))))
        deadline = time.monotonic() + args.wait_minutes * 60
        while not info.ended and time.monotonic() < deadline:
            sleep(POLL_SECONDS)
            info = claude.batch(info.id)
        if not info.ended:
            out(annotation("trial waiting", json.dumps({"batch_id": info.id, "counts": info.counts,
                                                         "next": "run again with --collect and the same events and arms"},
                                                        separators=(",", ":"))))
            return 0
        by_event = {w.event_id: w for w in works}
        results = []
        for r in claude.batch_results(info, model_of=model_of.get):
            m = CUSTOM_ID.fullmatch(r.custom_id)
            if m is None or int(m[1]) >= len(arms) or int(m[2]) not in by_event:
                out(f"::warning::result {r.custom_id!r} isn't one of this trial's requests (the same --events and --arms are needed)")
                continue
            results.append(judge(r, by_event[int(m[2])], int(m[1])))
        notes = f"development trial of the extraction on {len(events)} calls (#178, sitting 2); arms: {', '.join(a.name for a in arms)}"
        run_ids = record(conn, arms, results, info.id, notes)
        for no, arm in enumerate(arms):
            mine = sorted((x for x in results if x.arm_no == no), key=lambda x: x.event_id)
            total = sum((x.cost for x in mine), Decimal(0))
            out(annotation(f"trial arm {arm.name}", json.dumps(
                {"eval_run": run_ids[no] if run_ids else None, "cost_usd": f"{total:.4f}",
                 "results": [x.summary() for x in mine]}, separators=(",", ":"))))
        grand = sum((x.cost for x in results), Decimal(0))
        out(annotation("trial cost", json.dumps({"batch_id": info.id, "cost_usd": f"{grand:.4f}", "recorded": bool(run_ids),
                                                 "counts": dict(Counter(x.status for x in results))}, separators=(",", ":"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
