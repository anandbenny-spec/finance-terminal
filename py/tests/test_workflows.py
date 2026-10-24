"""The job workflows pick dev's or production's credentials from the `target` input (GitHub issue #2, item 1).

The env expressions are evaluated here with GitHub's expression rules (`&&` and `||` return an operand; '', 0,
null and false are falsy; a missing secret is ''; `==` ignores case and coerces mismatched types to numbers) for
every event and input, so the property is tested, not just the text: a manual run with no target, or with dev,
can't receive any production credential, even when a DEV_ secret is missing; a scheduled run always gets
production's."""
import math
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
JOBS = ("daily", "watch", "weekly", "refresh", "backfill")
SCHEDULED = ("daily", "watch", "weekly")
PER_TARGET = ("JOB_DATABASE_URL", "STORAGE_ENDPOINT", "STORAGE_ACCESS_KEY_ID", "STORAGE_SECRET_ACCESS_KEY")
SHARED = ("STORAGE_BUCKET", "STORAGE_REGION", "FRED_API_KEY", "SEC_USER_AGENT", "ANTHROPIC_API_KEY", "VOYAGE_API_KEY")

_TOKEN = re.compile(r"\s*(?:(\|\||&&|==|!=|!|\(|\))|'((?:[^']|'')*)'|([A-Za-z_][A-Za-z0-9_.\-]*))")


def _tokens(expr):
    out, pos, expr = [], 0, expr.strip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        assert m and m.end() > pos, f"unsupported expression syntax at {expr[pos:]!r}"
        out.append(("op", m[1]) if m[1] else ("str", m[2].replace("''", "'")) if m[2] is not None else ("id", m[3]))
        pos = m.end()
    return out


def _truthy(v):
    return not (v is None or v is False or v == "" or (isinstance(v, (int, float)) and (v == 0 or math.isnan(v))))


def _num(v):
    if v is None or v is False: return 0.0
    if v is True: return 1.0
    if isinstance(v, str):
        try: return float(v.strip() or 0)
        except ValueError: return math.nan
    return float(v)


def _eq(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    if type(a) is type(b):
        return a == b
    return _num(a) == _num(b)


def evaluate(expr, ctx):
    toks, i = _tokens(expr), 0

    def peek():
        return toks[i] if i < len(toks) else (None, None)

    def take():
        nonlocal i
        i += 1
        return toks[i - 1]

    def primary():
        kind, val = take()
        if (kind, val) == ("op", "("):
            v = either()
            assert take() == ("op", ")")
            return v
        if kind == "str":
            return val
        assert kind == "id", f"unexpected {val!r}"
        if val in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[val]
        root, *path = val.split(".")
        v = ctx[root]
        for p in path:
            v = v.get(p, "" if root == "secrets" else None) if isinstance(v, dict) else None
        return v

    def unary():
        if peek() == ("op", "!"):
            take()
            return not _truthy(unary())
        return primary()

    def compare():
        v = unary()
        if peek() in (("op", "=="), ("op", "!=")):
            op = take()[1]
            r = unary()
            v = _eq(v, r) if op == "==" else not _eq(v, r)
        return v

    def both():
        v = compare()
        while peek() == ("op", "&&"):
            take()
            r = compare()
            v = r if _truthy(v) else v
        return v

    def either():
        v = both()
        while peek() == ("op", "||"):
            take()
            r = both()
            v = v if _truthy(v) else r
        return v

    v = either()
    assert i == len(toks), f"trailing tokens in {expr!r}"
    return v


def interpolate(value, ctx):
    def s(v):
        return "" if v is None else ("true" if v is True else "false" if v is False else str(v))
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: s(evaluate(m[1], ctx)), str(value))


def load(name):
    wf = yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text())
    wf["on"] = wf.pop(True, wf.get("on"))           # PyYAML reads the key `on` as true
    return wf


def resolve(name, event, inputs, secrets):
    """Every env value the job's steps see, as GitHub would resolve it."""
    wf = load(name)
    job = wf["jobs"]["run"]
    base = {"github": {"event_name": event}, "inputs": inputs, "secrets": secrets, "env": {}}
    job_env = {k: interpolate(v, base) for k, v in job.get("env", {}).items()}
    ctx = {**base, "env": job_env}
    steps = [{k: interpolate(v, ctx) for k, v in s.get("env", {}).items()} for s in job["steps"]]
    return job_env, steps


def secrets(dev=True):
    s = {n: f"PROD:{n}" for n in PER_TARGET} | {n: f"SHARED:{n}" for n in SHARED}
    return s | ({f"DEV_{n}": f"DEV:{n}" for n in PER_TARGET} if dev else {})


def run_env(name, event, inputs, dev_secrets=True):
    job_env, steps = resolve(name, event, inputs, secrets(dev_secrets))
    run = next(e for e in steps if "JOB_DATABASE_URL" in e)
    every_value = [v for e in [job_env, *steps] for v in e.values()]
    return job_env["FT_TARGET"], run, every_value


def manual_inputs(name):
    """The inputs a manual dispatch gets when the target isn't given (GitHub fills in the defaults)."""
    spec = load(name)["on"]["workflow_dispatch"]["inputs"]
    return {k: v.get("default") for k, v in spec.items()}


@pytest.mark.parametrize("name", JOBS)
def test_target_input_defaults_to_dev(name):
    spec = load(name)["on"]["workflow_dispatch"]["inputs"]["target"]
    assert spec["type"] == "choice" and spec["options"] == ["dev", "production"] and spec["default"] == "dev"


@pytest.mark.parametrize("name", JOBS)
@pytest.mark.parametrize("inputs", ["defaults", {"target": "dev"}, {"target": "DEV"}, {"target": ""}, {}])
@pytest.mark.parametrize("dev_secrets", [True, False])
def test_a_manual_run_without_production_target_cannot_reach_production(name, inputs, dev_secrets):
    inputs = manual_inputs(name) if inputs == "defaults" else inputs
    target, run, values = run_env(name, "workflow_dispatch", inputs, dev_secrets)
    assert target == "dev"
    assert not [v for v in values if "PROD:" in v]                 # no production credential anywhere in the job
    for n in PER_TARGET:
        assert run[n] == (f"DEV:{n}" if dev_secrets else "")      # a missing DEV_ secret stays empty
    for n in SHARED:
        assert run[n] == f"SHARED:{n}"


@pytest.mark.parametrize("name", JOBS)
def test_a_manual_run_can_choose_production(name):
    target, run, values = run_env(name, "workflow_dispatch", {**manual_inputs(name), "target": "production"})
    assert target == "production" and not [v for v in values if "DEV:" in v]
    assert all(run[n] == f"PROD:{n}" for n in PER_TARGET)


@pytest.mark.parametrize("name", SCHEDULED)
@pytest.mark.parametrize("dev_secrets", [True, False])
def test_a_scheduled_run_always_targets_production(name, dev_secrets):
    target, run, values = run_env(name, "schedule", {}, dev_secrets)
    assert target == "production" and not [v for v in values if "DEV:" in v]
    assert all(run[n] == f"PROD:{n}" for n in PER_TARGET)
    assert "schedule" in load(name)["on"]


@pytest.mark.parametrize("name", JOBS)
def test_the_run_step_passes_the_target_and_takes_inputs_only_through_env(name):
    steps = load(name)["jobs"]["run"]["steps"]
    run = next(s for s in steps if s.get("name") == "Run")
    assert f'--workflow {name} --target "$FT_TARGET"' in run["run"]
    for s in steps:                                                # injection: no input pasted into a script
        assert "inputs." not in s.get("run", "") and "github.event.inputs" not in s.get("run", "")
    if name == "watch":
        assert '--as-of "$INPUT_AS_OF"' in run["run"] and run["env"]["INPUT_AS_OF"] == "${{ inputs.as_of }}"


def test_the_evaluator_follows_githubs_rules():
    ctx = {"secrets": {"A": "a"}, "inputs": {}, "env": {}, "github": {"event_name": "push"}}
    assert evaluate("secrets.MISSING", ctx) == "" and evaluate("inputs.target", ctx) is None
    assert evaluate("true && secrets.MISSING || 'fallback'", ctx) == "fallback"     # the trap the guard avoids
    assert evaluate("inputs.target == ''", ctx) is True                              # null == '' after coercion
    assert evaluate("'Production' == 'production'", ctx) is True
    assert evaluate("!(github.event_name == 'schedule') && secrets.A", ctx) == "a"


# ---------------------------------------------------------------- the freshness drill (Prompt 01 D4)

@pytest.mark.parametrize("inputs", [{"task": "policy.calendar", "action": "push", "target": "dev"}, {"task": "x"},
                                    {"task": "x", "target": ""}, {"task": "x", "target": "DEV"}])
@pytest.mark.parametrize("dev_secrets", [True, False])
def test_the_freshness_drill_reaches_production_only_when_chosen(inputs, dev_secrets):
    job_env, steps = resolve("freshness-drill", "workflow_dispatch", inputs, secrets(dev_secrets))
    drill = next(e for e in steps if "JOB_DATABASE_URL" in e)
    assert job_env["FT_TARGET"] == "dev"
    assert drill["JOB_DATABASE_URL"] == ("DEV:JOB_DATABASE_URL" if dev_secrets else "")
    assert not [v for e in [job_env, *steps] for v in e.values() if "PROD:" in v]
    job_env, steps = resolve("freshness-drill", "workflow_dispatch", {**inputs, "target": "production"}, secrets(dev_secrets))
    assert next(e for e in steps if "JOB_DATABASE_URL" in e)["JOB_DATABASE_URL"] == "PROD:JOB_DATABASE_URL"


def test_the_freshness_drill_is_manual_and_takes_inputs_only_through_env():
    wf = load("freshness-drill")
    assert list(wf["on"]) == ["workflow_dispatch"]
    assert wf["on"]["workflow_dispatch"]["inputs"]["target"]["default"] == "dev"
    assert wf["on"]["workflow_dispatch"]["inputs"]["action"]["default"] == "status"
    for s in wf["jobs"]["run"]["steps"]:
        assert "inputs." not in s.get("run", "") and "github.event.inputs" not in s.get("run", "")
    assert set(wf["jobs"]["run"]["steps"][-1]["env"]) == {"INPUT_TASK", "INPUT_ACTION", "INPUT_LAST_SUCCESS_AT",
                                                          "INPUT_LAST_CHECKED_AT", "JOB_DATABASE_URL"}
