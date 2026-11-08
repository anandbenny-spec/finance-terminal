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

An arm is `model:prompt:effort:max_tokens[:sN]`, for example `sonnet:v1:medium:16000` or `haiku:v2:low:24000`. Any part after the
model may be left out (`haiku:v2`): the prompt version defaults to `v1`, the effort and the cap to the task's. A last part `s3` reads
the call in 3 parts of about the same length, cut between turns: one request for each part, the same prompt and schema, every
quote still checked against the whole stored call; the parts' targets are joined and repeats marked. Why: a long transcript gets a
short list (8 Nov trial), and a request that sees a third of it can be asked to list everything in it.
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
MIN_SECTIONS, MAX_SECTIONS = 2, 6
DEFAULT_MAX_COST = Decimal("0.70")
DEFAULT_WAIT_MINUTES = 45
POLL_SECONDS = 30
EVAL_SET = "2b15_recall_trial"
SET_VERSION = "1"
CUSTOM_ID = re.compile(r"trial-a(\d+)-e(\d+)")
SECTION_ID = re.compile(r"trial-a(\d+)-e(\d+)-s(\d+)")


# ---------------------------------------------------------------------------------------------- arms and requests
@dataclass(frozen=True)
class Arm:
    model: str
    prompt: str = "v1"
    effort: str | None = None             # None: the task's
    max_tokens: int | None = None
    sections: int = 1                     # the call read in this many parts (one request each)

    @property
    def name(self) -> str:
        short = next((k for k, v in MODELS.items() if v == self.model), self.model)
        parts = [short, self.prompt, self.effort or "task", str(self.max_tokens or "task")]
        return ":".join(parts + ([f"s{self.sections}"] if self.sections > 1 else []))


def parse_arm(text: str) -> Arm:
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 5 or parts[0] not in MODELS:
        raise ValueError(f"arm {text!r}: expected model[:prompt[:effort[:max_tokens[:sN]]]], the model one of {sorted(MODELS)}")
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
    sections = 1
    if len(parts) > 4:
        m = re.fullmatch(r"s(\d+)", parts[4])
        if m is None or not MIN_SECTIONS <= int(m[1]) <= MAX_SECTIONS:
            raise ValueError(f"arm {text!r}: the last part must be s{MIN_SECTIONS} to s{MAX_SECTIONS} (the call read in that many parts)")
        sections = int(m[1])
    return Arm(MODELS[parts[0]], prompt, effort, cap, sections)


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


def split_turns(turns, n: int) -> list[list]:
    """`turns` (in call order) cut between turns into `n` runs of about the same number of characters. Always `n` runs when there
    are at least `n` turns, and no turn is left out or repeated."""
    turns = sorted(turns, key=lambda t: t.turn_no)
    n = max(1, min(n, len(turns)))
    size = [max(t.char_end - t.char_start, 1) for t in turns]
    total = sum(size)
    runs, run, seen = [], [], 0
    for i, t in enumerate(turns):
        run.append(t)
        seen += size[i]
        left = len(turns) - i - 1                         # turns after this one
        need = n - len(runs) - 1                          # runs still to start after this one
        if need > 0 and (seen >= total * (len(runs) + 1) / n or left == need) and left >= need:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return runs


def marked_section(call, turns) -> str:
    """The marked transcript of just `turns` (the same markers and the same text between turns as `ge.marked_text`)."""
    out, pos = [], None
    for t in turns:
        if pos is not None and t.char_start > pos:
            out.append(call.text[pos:t.char_start])
        out.append(ge.marker(t) + "\n" + call.text[max(t.char_start, pos or 0):t.char_end])
        pos = max(pos or 0, t.char_end)
    return "".join(out)


def build_requests(call, arm: Arm) -> list[Request]:
    """One request for an arm that reads the call whole, `arm.sections` for one that reads it in parts. A part's request is the
    whole call's request with only that part's turns as its source: the instruction, prompt and schema are the same."""
    whole = build_request(call, arm)
    if arm.sections == 1:
        return [whole]
    src = whole.sources[0]
    return [replace(whole, sources=(replace(src, text=marked_section(call, run)),)) for run in split_turns(call.turns, arm.sections)]


def worst_case_cost(req: Request, *, on=None) -> Decimal:
    """The batch price if the request used its whole token cap: the estimated input at the batch rate plus `max_tokens` of
    output (thinking included) at the batch rate."""
    prices, factor, _ = price_entry(req.model_id, on or datetime.now(UTC).date())
    p = prices["standard"]
    return ((Decimal(req.estimated_tokens()) * Decimal(str(p["input"])) + Decimal(req.spec.max_tokens) * Decimal(str(p["output"])))
            / MILLION * factor)


def custom_id(arm_no: int, event_id: int, section: int | None = None) -> str:
    return f"trial-a{arm_no}-e{event_id}" + ("" if section is None else f"-s{section}")


def parse_custom_id(cid: str) -> tuple[int, int, int | None] | None:
    m = SECTION_ID.fullmatch(cid)
    if m:
        return int(m[1]), int(m[2]), int(m[3])
    m = CUSTOM_ID.fullmatch(cid)
    return (int(m[1]), int(m[2]), None) if m else None


# ---------------------------------------------------------------------------------------------- results
@dataclass
class CallResult:
    arm_no: int
    event_id: int
    document_id: int
    status: str                            # checked | stopped | errored | canceled | expired | not_json | incomplete
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal(0)
    problems: tuple[str, ...] = ()
    targets: tuple[dict, ...] = ()         # kept targets, without their quotes
    stated: int = 0
    drops: tuple[tuple[str, int], ...] = ()
    parts: int = 1                         # the requests behind this result (an arm that reads the call in parts)

    @property
    def management(self) -> int:
        return sum(1 for t in self.targets if t["speaker_role"] == "management" and t["duplicate_of"] is None)

    def summary(self) -> dict:
        return {"event": self.event_id, "status": self.status, "stop": self.stop_reason, "parts": self.parts, "in": self.input_tokens,
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


def merge_parts(parts: list[CallResult], expected: int | None = None) -> CallResult:
    """The results of one call's parts as one result: the first part that failed names the status (a part that never came back
    makes it "incomplete"), tokens and cost add up, the targets are in document order with repeats (across parts too) marked in
    `check`'s way."""
    if len(parts) == 1 and expected in (None, 1):
        return parts[0]
    first = parts[0]
    bad = next((p for p in parts if p.status != "checked"), None)
    drops = Counter()
    for p in parts:
        drops.update(dict(p.drops))
    targets = []
    for p in parts:
        targets.extend(dict(t) for t in p.targets)
    targets.sort(key=lambda t: (t["speaker_role"] != "management", t["span"][0]))
    seen: dict[tuple, int] = {}
    for i, t in enumerate(targets):
        key = ge._target_key(t)
        if key in seen:
            t["duplicate_of"], t["not_scored_reason"] = seen[key], "duplicate"
        else:
            seen[key] = i
            t["duplicate_of"], t["not_scored_reason"] = None, ge.not_scored_reason(t)
    status = bad.status if bad else "incomplete" if expected and len(parts) < expected else "checked"
    return CallResult(first.arm_no, first.event_id, first.document_id, status, bad.stop_reason if bad else first.stop_reason,
                      sum(p.input_tokens for p in parts), sum(p.output_tokens for p in parts), sum((p.cost for p in parts), Decimal(0)),
                      tuple(x for p in parts for x in p.problems), tuple(targets), sum(p.stated for p in parts),
                      tuple(sorted(drops.items())), parts=len(parts))


def arm_metrics(arm: Arm, results: list[CallResult], batch_id: str) -> dict:
    return {"arm": arm.name, "effort": arm.effort, "max_tokens": arm.max_tokens, "sections": arm.sections, "batch_id": batch_id,
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
        # (arm number, call, part number or None, request): an arm that reads the call whole sends one request for it
        items = [(no, w, k if arm.sections > 1 else None, req) for no, arm in enumerate(arms) for w in works
                 for k, req in enumerate(build_requests(w.call, arm))]
        per_arm = {}
        for no, arm in enumerate(arms):
            mine = [r for n, _, _, r in items if n == no]
            per_arm[arm.name] = {"requests": len(mine), "estimated_input_tokens": sum(r.estimated_tokens() for r in mine),
                                 "worst_case_usd": f"{sum(worst_case_cost(r) for r in mine):.4f}"}
        worst = sum(worst_case_cost(r) for _, _, _, r in items)
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
        model_of = {custom_id(no, w.event_id, k): arms[no].model for no, w, k, _ in items}
        if args.collect:
            info = claude.batch(args.collect)
        else:
            info = claude.create_batch([(custom_id(no, w.event_id, k), req) for no, w, k, req in items])
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
        parts: dict[tuple[int, int], list[CallResult]] = {}
        for r in claude.batch_results(info, model_of=model_of.get):
            ids = parse_custom_id(r.custom_id)
            if ids is None or r.custom_id not in model_of or ids[1] not in by_event:
                out(f"::warning::result {r.custom_id!r} isn't one of this trial's requests (the same --events and --arms are needed)")
                continue
            parts.setdefault((ids[0], ids[1]), []).append(judge(r, by_event[ids[1]], ids[0]))
        results = [merge_parts(ps, arms[no].sections) for (no, _), ps in parts.items()]
        for (no, event), ps in parts.items():
            if len(ps) != arms[no].sections:
                out(f"::warning::arm {arms[no].name}, call {event}: {len(ps)} of {arms[no].sections} parts came back")
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
