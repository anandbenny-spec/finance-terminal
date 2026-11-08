"""The extraction trial (#178, sitting 2; py ft/equity/calls/trial.py): the arms and their parsing, that a trial request differs from
production's only where the arm says so (an arm of Haiku on v1 is production's request, byte for byte), that prompt v2 is v1 plus
the completeness section and nothing else, the worst-case price that stops a batch before it is sent, how a batch result is
judged, and what is written (no quote, once per batch). No database, no model and no network."""
import json
from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_ai_guidance_extraction import GROWTH, T, one

from ft.ai import guidance_extraction as ge
from ft.ai.client import HAIKU, MILLION, SONNET, BatchResult, Reply, Usage, load_prompt, load_schema
from ft.equity.calls import trial

METRICS = ge.load_metrics()
DAY = date(2026, 11, 8)


def reply(answer, *, stop: str = "end_turn", out: int = 900, cost: str = "0.0123", text: str | None = None) -> Reply:
    block = SimpleNamespace(type="text", text=json.dumps(answer) if text is None else text, citations=None)
    return Reply(HAIKU, "msg_1", stop, Usage(input=20_000, output=out), Decimal(cost), [block])


def work(call=None):
    call = call or one(GROWTH)
    return SimpleNamespace(event_id=3743, document_id=call.document_id, call=call, metrics=METRICS)


# ---------------------------------------------------------------------------------------------- arms
@pytest.mark.parametrize("text, arm", [
    ("sonnet:v1:medium:16000", trial.Arm(SONNET, "v1", "medium", 16_000)),
    ("haiku:v2", trial.Arm(HAIKU, "v2", None, None)),
    ("haiku", trial.Arm(HAIKU, "v1", None, None)),
    ("haiku:v2:low", trial.Arm(HAIKU, "v2", "low", None)),
    ("haiku::high:24000", trial.Arm(HAIKU, "v1", "high", 24_000)),
])
def test_an_arm_parses_with_defaults_for_what_is_left_out(text, arm):
    assert trial.parse_arm(text) == arm


@pytest.mark.parametrize("text", ["gpt:v1", "haiku:2", "haiku:v1:max", "haiku:v1:low:12", "haiku:v1:low:999999", "haiku:v1:low:x",
                                  "haiku:v1:low:16000:extra", ""])
def test_a_bad_arm_is_refused_before_anything_is_built(text):
    with pytest.raises(ValueError):
        trial.parse_arm(text)


def test_arms_are_split_on_spaces_or_commas_and_may_not_repeat():
    assert [a.name for a in trial.parse_arms("sonnet:v1:medium:16000, haiku:v2 haiku:v2:low")] == [
        "sonnet:v1:medium:16000", "haiku:v2:task:task", "haiku:v2:low:task"]
    with pytest.raises(ValueError, match="twice"):
        trial.parse_arms("haiku:v2 haiku:v2")
    with pytest.raises(ValueError, match="no arms"):
        trial.parse_arms("  ")


def test_events_are_distinct_whole_numbers():
    assert trial.parse_events("3743, 3780 4002") == [3743, 3780, 4002]
    with pytest.raises(ValueError):
        trial.parse_events("3743 3743")
    with pytest.raises(ValueError):
        trial.parse_events("")
    with pytest.raises(ValueError):
        trial.parse_events("3743 abc")


# ---------------------------------------------------------------------------------------------- requests
def production(call, version: str = "v1"):
    return ge.guidance_request(call, load_prompt(ge.TASK, version), load_schema(ge.TASK, version))


def test_haiku_on_v1_with_the_task_settings_is_the_production_request():
    call = one(GROWTH)
    req = trial.build_request(call, trial.Arm(HAIKU, "v1"))
    assert req.params(batch=True) == production(call).params(batch=True)
    assert req.input_sha256 == production(call).input_sha256


def test_an_arm_changes_the_model_the_effort_and_the_cap_and_nothing_else():
    call = one(GROWTH)
    base = production(call).params(batch=True)
    req = trial.build_request(call, trial.Arm(SONNET, "v1", "low", 24_000))
    got = req.params(batch=True)
    assert got["model"] == SONNET and got["max_tokens"] == 24_000 and got["output_config"]["effort"] == "low"
    skip = ("model", "max_tokens", "output_config")
    assert {k: v for k, v in got.items() if k not in skip} == {k: v for k, v in base.items() if k not in skip}
    assert {k: v for k, v in got["output_config"].items() if k != "effort"} == {
        k: v for k, v in base["output_config"].items() if k != "effort"}
    assert req.input_sha256 != production(call).input_sha256          # a different request is never taken for production's


def test_the_arms_of_one_call_have_different_hashes_so_nothing_is_deduplicated_between_them():
    call = one(GROWTH)
    hashes = {trial.build_request(call, a).input_sha256 for a in trial.parse_arms("haiku:v1 haiku:v2 sonnet:v1 haiku:v2:low")}
    assert len(hashes) == 4


def test_prompt_v2_is_v1_plus_the_completeness_section_and_nothing_else():
    v1, v2 = load_prompt(ge.TASK, "v1").text, load_prompt(ge.TASK, "v2").text
    start, end = v2.index("COMPLETENESS\n"), v2.index("TARGETS\nA target is")
    assert v2[:start] + v2[end:] == v1
    assert "turn by turn" in v2[start:end] and "drops those that fail" in v2[start:end]
    assert load_schema(ge.TASK, "v2") == load_schema(ge.TASK, "v1")      # v2 has no schema of its own: the same one is sent


def test_the_production_prompt_version_has_not_moved_to_v2():
    from ft.jobs.tasks.equity_calls_ai import PROMPT_VERSION
    assert PROMPT_VERSION[ge.TASK] == "v1"         # moving it is its own, deliberate change (a full re-extraction)


# ---------------------------------------------------------------------------------------------- the price
def test_the_worst_case_is_the_input_plus_the_whole_cap_at_the_batch_rate():
    req = trial.build_request(one(GROWTH), trial.Arm(SONNET, "v1", None, 16_000))
    # claude-sonnet-5-5 from 23 Oct 2026 (ai_prices.yaml): $2.00 in and $10.00 out per million, the batch at half
    expect = (Decimal(req.estimated_tokens()) * 2 + Decimal(16_000) * 10) / MILLION / 2
    assert trial.worst_case_cost(req, on=DAY) == expect
    haiku = trial.build_request(one(GROWTH), trial.Arm(HAIKU, "v1", None, 16_000))
    assert trial.worst_case_cost(haiku, on=DAY) == (Decimal(haiku.estimated_tokens()) * Decimal("0.10")
                                                    + Decimal(16_000) * Decimal("0.50")) / MILLION / 2
    assert trial.worst_case_cost(haiku, on=DAY) * 19 < trial.worst_case_cost(req, on=DAY) < trial.worst_case_cost(haiku, on=DAY) * 21


# ---------------------------------------------------------------------------------------------- results
def test_a_checked_reply_keeps_its_targets_without_their_quotes():
    w = work()
    r = trial.judge(BatchResult("trial-a1-e3743", "succeeded", reply({"targets": [T(GROWTH)], "stated_figures": []}, out=1234)), w, 1)
    assert (r.status, r.arm_no, r.event_id, r.input_tokens, r.output_tokens, r.cost) == (
        "checked", 1, 3743, 20_000, 1_234, Decimal("0.0123"))
    assert len(r.targets) == 1 and r.management == 1
    t = r.targets[0]
    assert "quote" not in t and t["span"][0] < t["span"][1] and t["metric"] == "revenue_growth" and t["value"] == "14"
    assert GROWTH not in json.dumps(r.summary())               # what is printed holds no transcript text


def test_a_reply_that_did_not_end_its_turn_is_a_failure_and_still_billed():
    r = trial.judge(BatchResult("trial-a0-e3743", "succeeded", reply({"targets": [T(GROWTH)], "stated_figures": []}, stop="max_tokens",
                                                                       out=16_000)), work(), 0)
    assert (r.status, r.stop_reason, r.output_tokens, r.cost, r.targets) == ("stopped", "max_tokens", 16_000, Decimal("0.0123"), ())


def test_a_reply_that_is_not_the_schemas_json_is_named_not_stored():
    r = trial.judge(BatchResult("trial-a0-e3743", "succeeded", reply(None, text="Here are the targets: ...")), work(), 0)
    assert r.status == "not_json" and r.targets == () and r.problems and r.cost == Decimal("0.0123")


def test_an_errored_or_expired_request_is_recorded_with_no_cost():
    e = trial.judge(BatchResult("trial-a0-e3743", "errored", error="overloaded_error"), work(), 0)
    x = trial.judge(BatchResult("trial-a0-e3743", "expired"), work(), 0)
    assert (e.status, e.problems, e.cost) == ("errored", ("overloaded_error",), Decimal(0))
    assert (x.status, x.problems, x.cost) == ("expired", (), Decimal(0))


def test_a_custom_id_names_its_arm_and_its_call():
    cid = trial.custom_id(2, 3743)
    assert trial.CUSTOM_ID.fullmatch(cid).groups() == ("2", "3743")
    assert len(cid) <= 64


# ---------------------------------------------------------------------------------------------- what is written
class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, recorded: bool = False):
        self.recorded, self.runs, self.results, self.next_id = recorded, [], [], 40

    def transaction(self):
        return nullcontext()

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("select 1 from ops.eval_run"):
            return Cursor((1,) if self.recorded else None)
        if s.startswith("insert into ops.eval_run"):
            self.next_id += 1
            self.runs.append((self.next_id, params))
            return Cursor((self.next_id,))
        if s.startswith("insert into ops.eval_result"):
            self.results.append(params)
            return Cursor(None)
        raise AssertionError(s)


def results_for_two_arms():
    one_target = trial.judge(BatchResult("c", "succeeded", reply({"targets": [T(GROWTH)], "stated_figures": []})), work(), 0)
    none = trial.judge(BatchResult("c", "expired"), work(), 1)
    return [one_target, none]


def test_a_trial_writes_one_run_per_arm_and_one_result_per_target_and_no_quote():
    conn = FakeConn()
    arms = trial.parse_arms("sonnet:v1:medium:16000 haiku:v2")
    ids = trial.record(conn, arms, results_for_two_arms(), "msgbatch_1", "a trial")
    assert ids == [41, 42]
    (_, first), (_, second) = conn.runs
    assert first[:5] == (trial.EVAL_SET, trial.SET_VERSION, SONNET, "v1", 1) and second[:5] == (trial.EVAL_SET, trial.SET_VERSION, HAIKU, "v2", 0)
    assert first[5].obj["batch_id"] == "msgbatch_1" and first[5].obj["arm"] == "sonnet:v1:medium:16000"
    assert [c["status"] for c in second[5].obj["calls"]] == ["expired"]
    assert len(conn.results) == 1
    run_id, key, predicted = conn.results[0]
    assert (run_id, key) == (41, "3743:0") and "quote" not in predicted.obj and predicted.obj["metric"] == "revenue_growth"
    assert GROWTH not in json.dumps([predicted.obj, first[5].obj, second[5].obj])


def test_a_batch_is_recorded_once():
    conn = FakeConn(recorded=True)
    assert trial.record(conn, trial.parse_arms("haiku:v2"), results_for_two_arms(), "msgbatch_1", "a trial") == []
    assert conn.runs == [] and conn.results == []


def test_the_arm_metrics_add_up_the_cost_and_say_no_one_judged_it():
    arms = trial.parse_arms("sonnet:v1:medium:16000")
    m = trial.arm_metrics(arms[0], results_for_two_arms()[:1] * 3, "msgbatch_1")
    assert m["cost_usd"] == "0.036900" and len(m["calls"]) == 3 and m["judge"].startswith("none")


# ---------------------------------------------------------------------------------------------- the command line
@pytest.mark.parametrize("argv", [["--events", "1", "--arms", "gpt:v1"], ["--events", "1 1", "--arms", "haiku"],
                                  ["--events", "1", "--arms", "haiku:v1:low:5"]])
def test_a_bad_command_line_stops_before_any_connection(argv):
    with pytest.raises(SystemExit) as e:
        trial.main(argv, env={"JOB_DATABASE_URL": "postgresql://nowhere/none"})
    assert e.value.code == 2


def test_no_dev_database_secret_stops_the_run():
    with pytest.raises(SystemExit) as e:
        trial.main(["--events", "1", "--arms", "haiku"], env={})
    assert e.value.code == 2


# ---------------------------------------------------------------------------------------------- the workflow
SEND_CASES = [({}, False), ({"send": False, "collect": ""}, False), ({"send": True, "collect": ""}, True),
              ({"send": False, "collect": "msgbatch_01abc"}, True), ({"send": True, "collect": "msgbatch_01abc"}, True)]


@pytest.mark.parametrize("dev_secrets", [True, False])
@pytest.mark.parametrize("inputs,key_present", SEND_CASES)
def test_the_workflow_reads_dev_only_and_holds_the_model_key_only_when_it_sends_or_collects(inputs, key_present, dev_secrets):
    from test_workflows import manual_inputs, resolve, secrets
    full = {**manual_inputs("extraction-trial"), **inputs}
    job_env, steps = resolve("extraction-trial", "workflow_dispatch", full, secrets(dev_secrets))
    step = next(e for e in steps if "JOB_DATABASE_URL" in e)
    assert job_env["FT_TARGET"] == "dev"
    assert step["JOB_DATABASE_URL"] == ("DEV:JOB_DATABASE_URL" if dev_secrets else "")
    assert step["ANTHROPIC_API_KEY"] == ("SHARED:ANTHROPIC_API_KEY" if key_present else "")
    assert not [v for e in [job_env, *steps] for v in e.values() if "PROD:" in v]
    assert set(step) == {"EVENTS", "ARMS", "MAX_COST", "SEND", "COLLECT", "JOB_DATABASE_URL", "ANTHROPIC_API_KEY"}


def test_the_first_dispatch_is_a_price_check_and_no_input_is_pasted_into_the_script():
    from test_workflows import load
    wf = load("extraction-trial")
    spec = wf["on"]["workflow_dispatch"]["inputs"]
    assert spec["send"]["type"] == "boolean" and spec["send"]["default"] is False and spec["collect"]["default"] == ""
    assert Decimal(spec["max_cost"]["default"]) == trial.DEFAULT_MAX_COST
    run = next(st["run"] for st in wf["jobs"]["run"]["steps"] if st.get("name") == "Trial")
    assert "${{" not in run and all(f'"${v}"' in run for v in ("EVENTS", "ARMS", "MAX_COST", "COLLECT"))
    assert wf["permissions"] == {"contents": "read"} and wf["jobs"]["run"]["timeout-minutes"] > trial.DEFAULT_WAIT_MINUTES


def test_the_workflow_defaults_parse_and_price_under_the_default_cap():
    from test_workflows import load
    spec = load("extraction-trial")["on"]["workflow_dispatch"]["inputs"]
    assert len(trial.parse_events(spec["events"]["default"])) == 5
    assert [a.name for a in trial.parse_arms(spec["arms"]["default"])] == [
        "sonnet:v1:medium:16000", "haiku:v2:medium:24000", "haiku:v2:low:16000"]
