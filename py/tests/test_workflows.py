"""The job workflows pick dev's or production's credentials from the `target` input (GitHub issue #2, item 1).

The env expressions are evaluated here with GitHub's expression rules (`&&` and `||` return an operand; '', 0,
null and false are falsy; a missing secret is ''; `==` ignores case and coerces mismatched types to numbers) for
every event and input, so the property is tested, not just the text: a manual run with no target, or with dev,
can't receive any production credential, even when a DEV_ secret is missing; a scheduled run always gets
production's."""
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
JOBS = ("daily", "watch", "weekly", "refresh", "backfill")
SCHEDULED = ("daily", "watch", "weekly")
KEEP_ALIVE = ("keep-alive", "keepalive-db")      # scheduled too, but not job workflows: they run no task (#142; #196 added the database backup)
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
            v = v.get(p, "" if root in ("secrets", "vars") else None) if isinstance(v, dict) else None
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
    s = {n: f"PROD:{n}" for n in PER_TARGET} | {n: f"SHARED:{n}" for n in (*SHARED, "COINGECKO_DEMO_KEY")}
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


# ---------------------------------------------------------------- the production-schedule guard (issue #69)

GUARD = "github.event_name != 'schedule' || vars.PRODUCTION_SCHEDULES == 'on'"


def scheduled_workflows():
    """Every workflow file with a `schedule:` trigger, found on disk, so one added later is covered too."""
    out = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        on = load(path.stem)["on"]
        if isinstance(on, dict) and "schedule" in on or on == "schedule" or isinstance(on, list) and "schedule" in on:
            out.append(path.stem)
    return out


def job_runs(name, event, variables):
    """Which of the workflow's jobs GitHub would start, for this event and these repository variables."""
    ctx = {"github": {"event_name": event}, "vars": variables, "inputs": {}, "secrets": {}, "env": {}}
    out = {}
    for job_id, job in load(name)["jobs"].items():
        cond = str(job.get("if", "true")).strip()
        cond = cond[3:-2] if cond.startswith("${{") and cond.endswith("}}") else cond
        out[job_id] = _truthy(evaluate(cond, ctx))
    return out


def test_the_scheduled_workflows_are_the_known_ones():
    # The keep-alive joined the list with #142; it carries the same guard, so every guard test below covers it too.
    assert scheduled_workflows() == sorted((*SCHEDULED, *KEEP_ALIVE))


@pytest.mark.parametrize("name", scheduled_workflows())
def test_every_job_of_a_scheduled_workflow_carries_the_guard(name):
    for job_id, job in load(name)["jobs"].items():
        assert str(job.get("if", "")).strip() == GUARD, f"{name}.yml job {job_id!r} lacks the schedule guard"


@pytest.mark.parametrize("name", scheduled_workflows())
@pytest.mark.parametrize("variables", [{}, {"PRODUCTION_SCHEDULES": ""}, {"PRODUCTION_SCHEDULES": "off"},
                                       {"PRODUCTION_SCHEDULES": "true"}, {"PRODUCTION_SCHEDULES": "1"},
                                       {"OTHER": "on"}])
def test_a_scheduled_run_does_nothing_unless_production_schedules_is_on(name, variables):
    assert not any(job_runs(name, "schedule", variables).values())


@pytest.mark.parametrize("name", scheduled_workflows())
def test_a_scheduled_run_starts_when_production_schedules_is_on(name):
    assert all(job_runs(name, "schedule", {"PRODUCTION_SCHEDULES": "on"}).values())


@pytest.mark.parametrize("name", scheduled_workflows())
@pytest.mark.parametrize("variables", [{}, {"PRODUCTION_SCHEDULES": "off"}, {"PRODUCTION_SCHEDULES": "on"}])
def test_a_manual_run_is_unaffected_by_the_guard(name, variables):
    assert all(job_runs(name, "workflow_dispatch", variables).values())


def test_the_guard_check_catches_an_unguarded_job():
    ctx = {"github": {"event_name": "schedule"}, "vars": {}, "inputs": {}, "secrets": {}, "env": {}}
    assert evaluate("vars.PRODUCTION_SCHEDULES", ctx) == ""                    # a missing variable is ''
    assert _truthy(evaluate("true", ctx))                                       # no `if:` means the job runs
    assert not _truthy(evaluate(GUARD, ctx))


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


@pytest.mark.parametrize("name", ("daily", "refresh"))
@pytest.mark.parametrize("target", ["dev", "production"])
def test_the_coingecko_demo_key_reaches_the_daily_and_refresh_runs_for_both_targets(name, target):
    """crypto.coingecko_markets (#106) uses one key for dev and production, passed as an environment variable."""
    _target, run, _values = run_env(name, "workflow_dispatch", {**manual_inputs(name), "target": target})
    assert run["COINGECKO_DEMO_KEY"] == "SHARED:COINGECKO_DEMO_KEY"


# ---------------------------------------------------------------- the keep-alive (#142)

KEPT_ALIVE = ["keep-alive.yml", "daily.yml", "weekly.yml", "watch.yml", "keepalive-db.yml"]   # the order the script visits them


def _run_keep_alive(tmp_path, states, put_fails=()):
    """Run the keep-alive's script with a stand-in `gh` that reports `states` ({file: state, None = unreadable}) and
    refuses the enable call for `put_fails`; returns (exit code, output, the workflows it asked GitHub to enable)."""
    log, cfg, gh = tmp_path / "calls.log", tmp_path / "gh.json", tmp_path / "gh"
    cfg.write_text(json.dumps({"states": states, "put_fails": list(put_fails), "log": str(log)}))
    gh.write_text(f"""#!{sys.executable}
import json, os, sys
cfg = json.load(open(os.environ["STUB_GH"]))
argv = sys.argv[1:]
open(cfg["log"], "a").write(" ".join(argv) + "\\n")
wf = next(a for a in argv if a.startswith("repos/")).split("/workflows/")[1].split("/")[0]
if "-X" in argv:
    sys.exit(1 if wf in cfg["put_fails"] else 0)
state = cfg["states"][wf]
if state is None:
    sys.exit(1)
print(state)
""")
    gh.chmod(0o755)
    script = load("keep-alive")["jobs"]["enable"]["steps"][0]["run"]
    env = {"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "GH_REPO": "owner/repo", "GH_TOKEN": "x",
           "STUB_GH": str(cfg)}
    done = subprocess.run(["bash", "-c", script], check=False, env=env, capture_output=True, text=True, timeout=30)
    calls = log.read_text().splitlines() if log.exists() else []
    return done.returncode, done.stdout, [c.split("/workflows/")[1].split("/")[0] for c in calls if "-X PUT" in c]


def test_the_keep_alive_holds_only_the_permission_it_needs_and_pushes_nothing():
    wf = load("keep-alive")
    assert wf["permissions"] == {} and list(wf["jobs"]) == ["enable"]
    job = wf["jobs"]["enable"]
    assert job["permissions"] == {"actions": "write"}
    assert job["env"]["GH_TOKEN"] == "${{ github.token }}"
    text = (WORKFLOWS / "keep-alive.yml").read_text()
    for forbidden in ("secrets.", "actions/checkout", "git push", "git commit", "contents: write"):
        assert forbidden not in text, forbidden


def test_the_keep_alive_names_every_scheduled_workflow():
    script = load("keep-alive")["jobs"]["enable"]["steps"][0]["run"]
    named = re.search(r"for wf in ([^;]+); do", script)[1].split()
    assert named == KEPT_ALIVE
    assert {n.removesuffix(".yml") for n in named} == set(scheduled_workflows())


@pytest.mark.parametrize("state", ["active", "disabled_inactivity"])
def test_the_keep_alive_enables_a_workflow_that_is_active_or_was_switched_off_for_inactivity(tmp_path, state):
    code, _out, enabled = _run_keep_alive(tmp_path, dict.fromkeys(KEPT_ALIVE, state))
    assert code == 0 and enabled == KEPT_ALIVE


def test_the_keep_alive_leaves_a_workflow_disabled_by_hand_alone(tmp_path):
    states = dict.fromkeys(KEPT_ALIVE, "active") | {"watch.yml": "disabled_manually", "daily.yml": "disabled_manually"}
    code, out, enabled = _run_keep_alive(tmp_path, states)
    assert code == 0 and enabled == ["keep-alive.yml", "weekly.yml", "keepalive-db.yml"]
    assert "daily.yml: disabled_manually, left alone" in out


def test_the_keep_alive_fails_when_it_cannot_read_a_workflow_and_still_handles_the_others(tmp_path):
    code, out, enabled = _run_keep_alive(tmp_path, dict.fromkeys(KEPT_ALIVE, "active") | {"weekly.yml": None})
    assert code == 1 and "::error title=keep-alive::could not read weekly.yml" in out
    assert enabled == ["keep-alive.yml", "daily.yml", "watch.yml", "keepalive-db.yml"]


def test_the_keep_alive_fails_when_github_refuses_to_enable_a_workflow(tmp_path):
    code, out, enabled = _run_keep_alive(tmp_path, dict.fromkeys(KEPT_ALIVE, "active"), put_fails=["daily.yml"])
    assert code == 1 and "could not enable daily.yml" in out
    assert enabled == KEPT_ALIVE


# ---------------------------------------------------------------- the watch schedules (issue #158)

def test_the_watch_runs_a_primary_inside_the_windows_lead_and_two_weekday_catch_ups_per_bank():
    """GitHub starts scheduled runs late and sometimes never (33 minutes late on 27 Oct; the 28 Oct FOMC run was not
    created). The primary starts inside the window's lead, so a start up to about 45 minutes late still polls; two
    catch-up runs follow on weekdays, after the window has closed (one pass each: due.py's catch-up)."""
    from datetime import date, datetime, time, timedelta

    from ft.jobs.due import BANKS, LEAD

    wf = load("watch")
    got = [(e["cron"], e["timezone"]) for e in wf["on"]["schedule"]]
    assert got == [("52 13 * * *", "America/New_York"), ("12 15 * * 1-5", "America/New_York"),
                   ("42 17 * * 1-5", "America/New_York"), ("52 9 * * *", "Asia/Kolkata"),
                   ("12 11 * * 1-5", "Asia/Kolkata"), ("42 13 * * 1-5", "Asia/Kolkata")]
    timeout = timedelta(minutes=wf["jobs"]["run"]["timeout-minutes"])

    def start(cron):
        minute, hour, *_ = cron.split()
        return datetime.combine(date(2030, 1, 1), time(int(hour), int(minute)))

    for bank in BANKS.values():
        primary, *catch_ups = [c for c, tz in got if tz == bank.tz.key]
        release = datetime.combine(date(2030, 1, 1), bank.release)
        assert primary.split()[2:] == ["*", "*", "*"]                       # every day, as before
        assert release - LEAD <= start(primary) < release                   # inside the lead: a late start still polls
        assert release + bank.window - start(primary) < timeout             # polling to the window's close fits
        assert len(catch_ups) == 2
        for cron in catch_ups:
            assert cron.split()[2:] == ["*", "*", "1-5"]                    # weekdays
            assert start(cron) >= release + bank.window                     # the window has closed: one pass


# ---------------------------------------------------------------- ci: the web job runs only when something it reads changed

def _web_step():
    steps = load("ci")["jobs"]["secrets"]["steps"]
    return next(s for s in steps if s.get("id") == "changed")


def test_the_web_job_follows_the_secrets_job_and_runs_only_when_it_says_so():
    """Anand, 29 Oct 2026: the free 2,000 Actions minutes ran out in 8 days. The web job is skipped (a skipped check
    counts as passed) when nothing it reads changed; the answer comes from the secrets job, which runs anyway, because a
    job of its own would be billed at least a minute per run."""
    jobs = load("ci")["jobs"]
    assert jobs["web"]["needs"] == "secrets" and jobs["web"]["if"] == "needs.secrets.outputs.web == 'true'"
    assert jobs["secrets"]["outputs"] == {"web": "${{ steps.changed.outputs.web }}"}
    assert "needs" not in jobs["test"] and "if" not in jobs["test"]               # the SQL and Python tests always run
    step = _web_step()
    assert "${{" not in step["run"]                                              # inputs reach the script through env
    assert set(step["env"]) == {"EVENT", "BASE_SHA", "BEFORE_SHA", "HEAD_SHA", "WEB_READS"}
    # what the web job reads: web/, the labelling set its tests read, vercel.json and ci.yml itself
    assert re.search(r"labelling_items\.json", (WORKFLOWS.parent.parent / "web/lib/labelling/labelling.test.ts").read_text())


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
                           *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _ask(tmp_path, event, changed, *, base="parent"):
    """Run the step's script in a throwaway repo whose last commit changes `changed`; returns its web output."""
    _git(tmp_path, "init", "-q", "-b", "main")
    for f in ("py/a.py", "web/lib/x.ts", "README.md"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("1")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    parent = _git(tmp_path, "rev-parse", "HEAD")
    for f in changed:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("2")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "change")
    out = tmp_path / "output.txt"
    step = _web_step()
    env = {**os.environ, "EVENT": event, "HEAD_SHA": _git(tmp_path, "rev-parse", "HEAD"), "GITHUB_OUTPUT": str(out),
           "BASE_SHA": {"parent": parent, "zeros": "0" * 40, "unknown": "f" * 40, "none": ""}[base],
           "BEFORE_SHA": {"parent": parent, "zeros": "0" * 40, "unknown": "f" * 40, "none": ""}[base],
           "WEB_READS": step["env"]["WEB_READS"]}
    done = subprocess.run(["bash", "-e", "-c", step["run"]], check=False, cwd=tmp_path, env=env, capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stderr
    return out.read_text().strip()


@pytest.mark.parametrize("event", ["pull_request", "push"])
@pytest.mark.parametrize("changed, web", [
    (["py/src/ft/jobs/due.py"], "false"),
    (["py/config/tasks.yaml", "supabase/migrations/20261029000000_x.sql", "README.md", "CLAUDE.md"], "false"),
    ([".github/workflows/watch.yml", ".github/workflows/ci.yml.bak"], "false"),
    (["web2/page.tsx", "py/web/page.py", "docs/vercel.json"], "false"),
    (["web/app/page.tsx"], "true"),
    (["py/src/ft/jobs/due.py", "web/lib/x.ts"], "true"),
    (["py/config/eval/sets/labelling_items.json"], "true"),
    (["vercel.json"], "true"),
    ([".github/workflows/ci.yml"], "true"),
])
def test_the_web_job_runs_when_a_file_it_reads_changed(tmp_path, event, changed, web):
    assert _ask(tmp_path, event, changed) == f"web={web}"


@pytest.mark.parametrize("event, base", [("workflow_dispatch", "parent"), ("push", "zeros"), ("push", "unknown"),
                                         ("pull_request", "none"), ("schedule", "parent")])
def test_in_any_doubt_the_web_job_runs(tmp_path, event, base):
    # a manual run, a first push (before is all zeros), a base the clone doesn't have, no base, any other event
    assert _ask(tmp_path, event, ["py/src/ft/jobs/due.py"], base=base) == "web=true"


# ---------------------------------------------------------------- the database keep-alive backup (issue #196)

KEEPALIVE_QUERY = "select count(*) from core.series"


def _keepalive_db_script():
    return load("keepalive-db")["jobs"]["ping"]["steps"][0]["run"]


KEEPALIVE_URL = "postgresql://job_writer.ref@pooler.example:5432/postgres?application_name=marker-7"      # no password in it (the scanner's rule), a marker to prove it is never echoed


def _run_keepalive_db(tmp_path, *, url=KEEPALIVE_URL, stdout="42\n", exit_code=0, stderr="", with_psql=True):
    """Run the step's script with a stand-in `psql`; returns (exit code, output, the arguments psql got or None)."""
    log = tmp_path / "psql.log"
    if with_psql:
        psql = tmp_path / "psql"
        psql.write_text(f"""#!{sys.executable}
import json, sys
open({str(log)!r}, "w").write(json.dumps(sys.argv[1:]))
sys.stdout.write({stdout!r})
sys.stderr.write({stderr!r})
sys.exit({exit_code})
""")
        psql.chmod(0o755)
    env = {"PATH": f"{tmp_path}{os.pathsep}/usr/bin:/bin" if with_psql else "/nonexistent-bin-dir"}
    if url is not None:
        env["JOB_DATABASE_URL"] = url
    bash = next(p for p in ("/usr/bin/bash", "/bin/bash") if Path(p).exists())
    done = subprocess.run([bash, "-c", _keepalive_db_script()], check=False, env=env, capture_output=True, text=True,
                          timeout=30)
    return done.returncode, done.stdout + done.stderr, json.loads(log.read_text()) if log.exists() else None


def test_the_database_keep_alive_is_scheduled_twice_a_day_and_can_be_started_by_hand():
    wf = load("keepalive-db")
    assert wf["on"]["schedule"] == [{"cron": "23 1,13 * * *"}]                  # 06:53 and 18:53 IST, in UTC
    assert "workflow_dispatch" in wf["on"]
    assert list(wf["jobs"]) == ["ping"]


def test_the_database_keep_alive_holds_no_permission_and_checks_out_nothing():
    wf = load("keepalive-db")
    job = wf["jobs"]["ping"]
    assert wf["permissions"] == {} and job["permissions"] == {} and job["timeout-minutes"] == 5
    text = (WORKFLOWS / "keepalive-db.yml").read_text()
    for forbidden in ("actions/checkout", "git push", "contents:", "GITHUB_TOKEN", "github.token", "DEV_", "inputs."):
        assert forbidden not in text, forbidden


def test_the_database_keep_alive_reads_production_with_the_secret_the_daily_workflow_uses():
    job = load("keepalive-db")["jobs"]["ping"]
    assert job["env"]["JOB_DATABASE_URL"] == "${{ secrets.JOB_DATABASE_URL }}"
    # the daily workflow passes exactly this secret to a scheduled (production) run
    daily = next(e for e in resolve("daily", "schedule", {}, secrets())[1] if "JOB_DATABASE_URL" in e)
    assert daily["JOB_DATABASE_URL"] == "PROD:JOB_DATABASE_URL"
    assert interpolate(job["env"]["JOB_DATABASE_URL"], {"secrets": secrets(), "env": {}}) == "PROD:JOB_DATABASE_URL"
    for step in job["steps"]:                      # the secret never sits on a run line
        assert "secrets." not in step.get("run", "") and "${{" not in step.get("run", "")


def test_the_database_keep_alive_runs_one_read_only_query():
    script = _keepalive_db_script()
    assert script.count("--command") == 1 and f'--command "{KEEPALIVE_QUERY}"' in script
    assert "--set ON_ERROR_STOP=1" in script
    lowered = script.lower()
    for word in ("insert ", "update ", "delete ", "drop ", "alter ", "create ", "truncate ", "grant "):
        assert word not in lowered, word


def test_the_database_keep_alive_passes_the_url_from_the_environment_and_prints_the_count(tmp_path):
    code, out, argv = _run_keepalive_db(tmp_path)
    assert code == 0 and "core.series rows: 42" in out and "::warning" not in out
    assert argv[0] == KEEPALIVE_URL
    assert argv[argv.index("--command") + 1] == KEEPALIVE_QUERY and "ON_ERROR_STOP=1" in argv
    assert "marker-7" not in out                                                 # the url is never echoed


def test_the_database_keep_alive_warns_when_the_catalogue_is_empty_but_still_succeeds(tmp_path):
    code, out, _argv = _run_keepalive_db(tmp_path, stdout="0\n")
    assert code == 0 and "::warning title=keepalive-db::core.series is empty" in out


@pytest.mark.parametrize("exit_code", [1, 2, 3])
def test_the_database_keep_alive_fails_without_printing_psqls_own_message(tmp_path, exit_code):
    leak = 'missing "=" after "postgresql://job_writer.ref@pooler.example?application_name=marker-7" in connection info string'
    code, out, _argv = _run_keepalive_db(tmp_path, exit_code=exit_code, stderr=leak, stdout="")
    assert code == 1 and f"::error title=keepalive-db::the read-only query failed (psql exit {exit_code})" in out
    assert "marker-7" not in out and "missing" not in out


@pytest.mark.parametrize("stdout", ["", "not a number\n", "4 2\n", "-1\n"])
def test_the_database_keep_alive_fails_when_the_answer_is_not_a_count(tmp_path, stdout):
    code, out, _argv = _run_keepalive_db(tmp_path, stdout=stdout)
    assert code == 1 and "::error title=keepalive-db::the query did not return a number" in out


@pytest.mark.parametrize("url", [None, ""])
def test_the_database_keep_alive_fails_when_the_secret_is_missing_and_never_calls_psql(tmp_path, url):
    code, out, argv = _run_keepalive_db(tmp_path, url=url)
    assert code == 1 and "::error title=keepalive-db::JOB_DATABASE_URL is not set" in out and argv is None


def test_the_database_keep_alive_says_so_when_psql_is_missing(tmp_path):
    code, out, argv = _run_keepalive_db(tmp_path, with_psql=False)
    assert code == 1 and "::error title=keepalive-db::psql is not on this runner image" in out and argv is None
