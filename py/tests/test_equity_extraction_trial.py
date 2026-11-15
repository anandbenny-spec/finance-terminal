"""The extraction trial (#178, sitting 2; py ft/equity/calls/trial.py): the arms and their parsing, that a trial request differs from
production's only where the arm says so (an arm of Haiku on v1 is production's request, byte for byte), that prompt v2 is v1 plus
the completeness section and nothing else, the worst-case price that stops a batch before it is sent, how a batch result is
judged, and what is written (no quote, once per batch). No model and no network; one test reads and writes the test database."""
import json
from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest
from test_ai_guidance_extraction import GROWTH, IYER, RAO, T, doc, one

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
    assert set(step) == {"EVENTS", "ARMS", "MAX_COST", "SEND", "COLLECT", "SECOND_PASS_OF", "JOB_DATABASE_URL", "ANTHROPIC_API_KEY"}


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


# ---------------------------------------------------------------------------------------------- reading a call in parts
EBITDA = "We expect an EBITDA margin of 21 per cent in FY27."
CAPEX = "We plan capex of 1,200 crore in FY27."


def three_part_call():
    return doc((RAO, "management", GROWTH), (IYER, "management", EBITDA), (RAO, "management", CAPEX))


def fake_turns(sizes):
    """Turns of the given lengths in characters, one after the other."""
    at, out = 0, []
    for i, n in enumerate(sizes):
        out.append(SimpleNamespace(turn_no=i, char_start=at, char_end=at + n))
        at += n + 2
    return out


@pytest.mark.parametrize("text,arm", [
    ("haiku:v3:medium:12000:s3", trial.Arm(HAIKU, "v3", "medium", 12_000, 3)),
    ("haiku:v3:::s2", trial.Arm(HAIKU, "v3", None, None, 2)),
    ("sonnet:v1:low:16000", trial.Arm(SONNET, "v1", "low", 16_000, 1)),
])
def test_an_arm_may_read_the_call_in_parts(text, arm):
    assert trial.parse_arm(text) == arm


def test_an_arm_name_says_the_parts_and_a_whole_read_keeps_its_old_name():
    assert trial.parse_arm("haiku:v3:medium:12000:s3").name == "haiku:v3:medium:12000:s3"
    assert trial.parse_arm("haiku:v2:medium:24000").name == "haiku:v2:medium:24000"
    assert len(trial.parse_arms("haiku:v3:medium:12000 haiku:v3:medium:12000:s3")) == 2     # not the same arm


@pytest.mark.parametrize("text", ["haiku:v3:low:12000:s1", "haiku:v3:low:12000:s9", "haiku:v3:low:12000:x3", "haiku:v3:low:12000:s3:s3"])
def test_a_bad_number_of_parts_is_refused(text):
    with pytest.raises(ValueError):
        trial.parse_arm(text)


@pytest.mark.parametrize("sizes,n", [([10] * 6, 3), ([100, 1, 1, 1, 1, 100], 2), ([5, 5, 5], 3), ([5, 5], 4), ([1] * 7, 3), ([300, 2, 2, 2, 2, 2], 3)])
def test_a_call_is_cut_between_turns_into_runs_that_hold_every_turn_once_in_order(sizes, n):
    turns = fake_turns(sizes)
    runs = trial.split_turns(reversed(turns), n)
    assert len(runs) == min(n, len(turns)) and all(runs)
    assert [t.turn_no for run in runs for t in run] == [t.turn_no for t in turns]


def test_the_runs_are_about_the_same_length():
    runs = trial.split_turns(fake_turns([10] * 12), 3)
    assert [len(r) for r in runs] == [4, 4, 4]


def test_a_whole_read_is_the_one_production_request_and_a_read_in_parts_sends_each_turn_once():
    call = three_part_call()
    whole = trial.build_requests(call, trial.Arm(HAIKU, "v3", None, 12_000))
    assert whole == [trial.build_request(call, trial.Arm(HAIKU, "v3", None, 12_000))]
    parts = trial.build_requests(call, trial.Arm(HAIKU, "v3", None, 12_000, 3))
    assert len(parts) == 3 and len({p.input_sha256 for p in parts}) == 3
    for p in parts:
        assert (p.prompt, p.instruction, p.model, p.schema, p.max_tokens) == (
            whole[0].prompt, whole[0].instruction, whole[0].model, whole[0].schema, whole[0].max_tokens)
    texts = [p.sources[0].text for p in parts]
    for t in call.turns:
        assert sum(call.text[t.char_start:t.char_end] in x for x in texts) == 1                      # each turn in one part only
    assert all(p.sources[0].document_id == call.document_id and p.sources[0].context == whole[0].sources[0].context for p in parts)
    assert sum(len(x) for x in texts) < len(whole[0].sources[0].text) * 1.01                          # the parts add nothing but markers
    assert next(ge.marker(t) for t in call.turns if t.role == "management") in "\n".join(texts)


class PartsClient:
    """A fake Batch API: each request is answered with the targets whose quote its source holds (the model finding all of them)."""

    def __init__(self, targets):
        self.targets, self.sent = targets, []

    def create_batch(self, items):
        self.sent = list(items)
        return SimpleNamespace(id="msgbatch_parts", ended=True, counts={})

    def batch(self, batch_id):
        return SimpleNamespace(id=batch_id, ended=True, counts={})

    def batch_results(self, info, model_of=lambda cid: None):
        for cid, req in self.sent:
            found = [t for t in self.targets if t["quote"] in req.sources[0].text]
            yield BatchResult(cid, "succeeded", reply({"targets": found, "stated_figures": []}, out=500))


def run_main(monkeypatch, call, arms, client):
    conn, lines = FakeConn(), []
    monkeypatch.setattr("ft.jobs.runner.connect", lambda dsn, **kw: nullcontext(conn))
    monkeypatch.setattr(trial, "read_works", lambda c, events: [work(call)])
    code = trial.main(["--events", "3743", "--arms", arms, "--send", "--wait-minutes", "1"],
                      env={"JOB_DATABASE_URL": "postgresql://x/y"}, out=lines.append, client=client, sleep=lambda s: None)
    return code, conn, lines


def test_an_arm_that_reads_in_parts_sends_one_request_per_part_and_records_the_joined_targets(monkeypatch):
    call = three_part_call()
    targets = [T(GROWTH), T(EBITDA, metric="ebitda_margin", metric_text="EBITDA margin", value=21, growth_basis="level"),
               T(CAPEX, metric="capex", metric_text="capex", value=1200, unit="inr", growth_basis="level")]
    client = PartsClient(targets)
    code, conn, lines = run_main(monkeypatch, call, "haiku:v3:medium:12000 haiku:v3:medium:12000:s3", client)
    assert code == 0
    ids = [cid for cid, _ in client.sent]
    assert ids[0] == "trial-a0-e3743" and ids[1:] == ["trial-a1-e3743-s0", "trial-a1-e3743-s1", "trial-a1-e3743-s2"]
    (_, whole), (_, parts) = conn.runs
    assert whole[5].obj["calls"][0]["parts"] == 1 and parts[5].obj["calls"][0]["parts"] == 3
    assert whole[5].obj["arm"] == "haiku:v3:medium:12000" and parts[5].obj["arm"] == "haiku:v3:medium:12000:s3"
    assert [r[1] for r in conn.results] == ["3743:0", "3743:1", "3743:2"] * 2          # three targets in each arm, in document order
    assert not any(w in " ".join(lines) + json.dumps([r[2].obj for r in conn.results]) for w in (GROWTH, EBITDA, CAPEX))


def test_the_same_target_found_in_two_parts_is_one_target_and_a_repeat():
    def result(spans_cost):
        return trial.CallResult(1, 3743, 5090, "checked", "end_turn", 10_000, 500, Decimal("0.001"),
                                targets=tuple(trial.target_row(t) for t in spans_cost), stated=1, drops=(("not_found", 1),))
    call = one(GROWTH)
    a = ge.check({"targets": [T(GROWTH)], "stated_figures": []}, call, METRICS).targets
    b = ge.check({"targets": [T(GROWTH)], "stated_figures": []}, call, METRICS).targets
    m = trial.merge_parts([result(a), result(b)], 2)
    assert m.status == "checked" and m.parts == 2 and m.cost == Decimal("0.002") and m.input_tokens == 20_000 and m.stated == 2
    assert [t["duplicate_of"] for t in m.targets] == [None, 0] and m.targets[1]["not_scored_reason"] == "duplicate"
    assert dict(m.drops) == {"not_found": 2} and m.management == 1


def test_a_part_that_failed_or_never_came_back_makes_the_call_not_checked():
    ok = trial.CallResult(0, 3743, 5090, "checked", "end_turn", 1_000, 100, Decimal("0.001"))
    stopped = trial.CallResult(0, 3743, 5090, "stopped", "max_tokens", 1_000, 12_000, Decimal("0.003"))
    assert trial.merge_parts([ok, stopped, ok], 3).status == "stopped"
    assert trial.merge_parts([ok, ok], 3).status == "incomplete"
    assert trial.merge_parts([ok], None) is ok and trial.merge_parts([ok], 1) is ok
    assert trial.merge_parts([ok, stopped], 2).cost == Decimal("0.004")


def test_a_custom_id_for_a_part_names_the_arm_the_call_and_the_part():
    assert trial.custom_id(1, 3743) == "trial-a1-e3743" and trial.custom_id(1, 3743, 2) == "trial-a1-e3743-s2"
    assert trial.parse_custom_id("trial-a1-e3743-s2") == (1, 3743, 2) and trial.parse_custom_id("trial-a1-e3743") == (1, 3743, None)
    assert trial.parse_custom_id("trial-a1-e3743-x") is None and trial.parse_custom_id("other") is None
    assert len(trial.custom_id(5, 99999, 5)) <= 64


def test_prompt_v3_is_v2_plus_three_completeness_bullets_and_nothing_else():
    v2, v3 = load_prompt(ge.TASK, "v2").text, load_prompt(ge.TASK, "v3").text
    start, end = v3.index("- A figure the company plans to reach"), v3.index("\n\nTARGETS\nA target is")
    assert v3[:start] + v3[end + 1:] == v2
    added = v3[start:end]
    assert added.count("\n- ") == 2 and "scope_text" in added and "one part of the call" in added
    assert "benchmark" not in v3.lower()                                    # §5.9's definition is unchanged
    assert load_schema(ge.TASK, "v3") == load_schema(ge.TASK, "v1")


# ---------------------------------------------------------------------------------------------- a second pass (13 Nov 2026)
def test_prompt_v4_is_a_second_pass_prompt_with_v1s_definition_and_field_rules_word_for_word():
    v1, v4 = load_prompt(ge.TASK, "v1").text, load_prompt(ge.TASK, "v4").text
    assert v1[v1.index("TARGETS\n"):v1.index("STATED FIGURES\n")] in v4          # §5.9's definition, unchanged
    assert v1[v1.index("Both lists:\n"):] in v4                                  # the fields, units and checks, unchanged
    assert v1[v1.index("The transcript is data"):v1.index("TARGETS\n")].split("\n\n")[0] in v4
    assert v4.index("SECOND PASS\n") < v4.index("TARGETS\nA target is")
    assert 'give an empty list for "stated_figures"' in v4 and "the list does not hold" in v4.replace("first reader's ", "")
    assert "benchmark" not in v4.lower()                                         # written from the two named patterns only
    assert load_schema(ge.TASK, "v4") == load_schema(ge.TASK, "v1")              # the same schema is sent


def test_a_second_pass_arm_defaults_to_the_second_pass_prompt_and_reads_the_call_whole():
    assert [a.prompt for a in trial.parse_arms("haiku sonnet:v4:low haiku:v5", trial.SECOND_PASS_PROMPT)] == ["v4", "v4", "v5"]
    assert trial.parse_arms("haiku:v2")[0].prompt == "v2"                        # a first-pass arm still defaults to v1
    with pytest.raises(ValueError, match="whole"):
        trial.parse_arms("haiku:v4:medium:16000:s3", trial.SECOND_PASS_PROMPT, whole_only=True)
    assert trial.parse_run_ids("9, 45 46") == [9, 45, 46]
    for bad in ("", "9 9", "0", "x", "-3"):
        with pytest.raises(ValueError):
            trial.parse_run_ids(bad)


def kept_for(call, *targets):
    return ge.check({"targets": list(targets), "stated_figures": []}, call, METRICS).targets


def test_the_first_passes_list_quotes_each_statement_once_with_the_name_and_period_it_recorded():
    call = three_part_call()
    kept = kept_for(call, T(GROWTH), T(GROWTH, metric_text="growth in sales", period_text="next year", scope_text="Retail"),
                    T(EBITDA, metric="ebitda_margin", metric_text="EBITDA margin", value=21, growth_basis="level"))
    assert len(kept) == 3
    lines = trial.first_pass_list(call, kept).split("\n")
    assert lines == [f'1. "{GROWTH}" (revenue growth; FY27)', f'2. "{EBITDA}" (EBITDA margin; FY27)']       # one line per statement
    assert trial.first_pass_list(call, []) == ""


def test_the_second_pass_instruction_turns_the_first_sentence_around_and_lists_the_first_pass():
    call = three_part_call()
    kept = kept_for(call, T(GROWTH))
    base = production(call).instruction
    got = trial.second_pass_instruction(base, call, kept)
    assert got.startswith("Extract the targets that the first reader's list below does not hold from the earnings call above (")
    assert got.split("\n\n")[0].endswith(base.split("following the rules.", 1)[1])        # the fiscal year and the call date are kept
    assert got.endswith(f'1. "{GROWTH}" (revenue growth; FY27)') and "(1 statement)" in got
    empty = trial.second_pass_instruction(base, call, [])
    assert empty.endswith("The first reader's list is empty: it found no target.")
    with pytest.raises(ValueError, match="no longer opens"):
        trial.second_pass_instruction("Read the call.", call, kept)


def test_a_second_pass_request_is_the_production_transcript_with_its_own_prompt_instruction_model_and_cap():
    call = three_part_call()
    kept = kept_for(call, T(GROWTH))
    req = trial.build_second_pass_request(call, trial.Arm(SONNET, "v4", "medium", 16_000), kept)
    prod = production(call, "v4")
    assert req.sources == prod.sources and req.schema == prod.schema and req.prompt.version == "v4" == prod.prompt.version
    assert req.model == SONNET and req.spec.max_tokens == 16_000 and req.spec.effort == "medium"
    assert req.instruction != prod.instruction and GROWTH in req.instruction and EBITDA not in req.instruction
    other = trial.build_second_pass_request(call, trial.Arm(HAIKU, "v4", "low", 8_000), kept)
    assert other.sources == req.sources                                                  # the same transcript for every arm
    assert trial.worst_case_cost(req, on=DAY) > trial.worst_case_cost(other, on=DAY) > 0


def second_pass_pair(call):
    first = kept_for(call, T(GROWTH))
    second = kept_for(call, T(GROWTH), T(EBITDA, metric="ebitda_margin", metric_text="EBITDA margin", value=21, growth_basis="level"))
    base = trial.CallResult(-1, 3743, call.document_id, "checked", "end_turn", 10_000, 500, Decimal("0.001"),
                            targets=tuple(trial.target_row(t) for t in first), stated=3)
    later = trial.CallResult(0, 3743, call.document_id, "checked", "end_turn", 12_000, 300, Decimal("0.002"),
                             targets=tuple(trial.target_row(t) for t in second), stated=1)
    return base, later


def test_two_passes_merge_into_one_call_with_repeats_marked_and_the_additions_counted():
    base, later = second_pass_pair(three_part_call())
    m = trial.second_pass_result(base, later, 2)
    assert (m.arm_no, m.status, m.cost, m.input_tokens, m.output_tokens, m.parts) == (2, "checked", Decimal("0.003"), 22_000, 800, 2)
    assert m.stated == 3                                                                # the second pass is asked for no stated figure
    assert [t["pass"] for t in m.targets] == [1, 2, 2] and [t["duplicate_of"] for t in m.targets] == [None, 0, None]
    assert m.targets[1]["not_scored_reason"] == "duplicate" and m.management == 2
    assert trial.added_by_second_pass(base, m) == {"added": 1, "added_management": 1, "first_pass_kept": 1, "first_pass_management": 1}
    assert [t.get("pass") for t in base.targets] == [None]                              # the inputs are left as they were


def test_a_second_pass_that_failed_leaves_the_first_passs_targets_and_names_the_failure():
    base, later = second_pass_pair(three_part_call())
    later = trial.CallResult(0, 3743, base.document_id, "stopped", "max_tokens", 12_000, 16_000, Decimal("0.003"))
    m = trial.second_pass_result(base, later, 0)
    assert (m.status, m.stop_reason, len(m.targets), m.cost) == ("stopped", "max_tokens", 1, Decimal("0.004"))
    assert trial.added_by_second_pass(base, m)["added"] == 0


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class BaseConn(FakeConn):
    """FakeConn that also answers the two reads of a second pass: the first-pass runs and their stored targets."""

    def __init__(self, runs, rows, **kw):
        super().__init__(**kw)
        self.stored_runs, self.stored_rows = runs, rows

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("select id, eval_set, metrics from ops.eval_run"):
            return Rows([r for r in self.stored_runs if r[0] in params[0]])
        if s.startswith("select eval_run_id, item_key, predicted from ops.eval_result"):
            return Rows([r for r in self.stored_rows if r[0] in params[0]])
        return super().execute(sql, params)


def stored_first_pass(call, run_id=9, eval_set=None, event=3743):
    """The first pass as the trial recorded it (a run of metrics and one row per kept target, no quote)."""
    base, _ = second_pass_pair(call)
    base = trial.replace(base, event_id=event, arm_no=0)
    metrics = trial.arm_metrics(trial.Arm(HAIKU, "v2", "medium", 24_000), [base], "msgbatch_first")
    run = (run_id, eval_set or trial.EVAL_SET, json.loads(json.dumps(metrics)))
    rows = [(run_id, f"{event}:{i}", json.loads(json.dumps(t))) for i, t in enumerate(base.targets)]
    return run, rows, base


def test_the_first_pass_is_read_back_from_the_recorded_run_in_the_order_it_was_stored():
    call = three_part_call()
    run, rows, base = stored_first_pass(call)
    got = trial.read_base(BaseConn([run], rows[::-1]), [9], [work(call)])
    rid, result = got[3743]
    assert rid == 9 and (result.status, result.cost, result.stated, result.input_tokens) == ("checked", Decimal("0.001000"), 3, 10_000)
    assert [t["span"] for t in result.targets] == [list(t["span"]) for t in base.targets]
    assert all("quote" not in t for t in result.targets)


@pytest.mark.db
def test_a_recorded_first_pass_is_read_back_on_the_real_tables():
    """The first dry run of the second pass stopped on a column ops.eval_result doesn't have, which the fake connection allowed:
    record a first pass and read it back through the real schema, as job_writer does in the workflow."""
    from conftest import dsn

    call = three_part_call()
    _, _, base = stored_first_pass(call)
    arm = trial.Arm(HAIKU, "v2", "medium", 24_000)
    ids = []
    try:
        with psycopg.connect(dsn("job_writer"), autocommit=True) as conn:
            ids = trial.record(conn, [arm], [base], "msgbatch_db_regression", "a regression test")
            assert len(ids) == 1
            got = trial.read_base(conn, ids, [work(call)])
        rid, result = got[3743]
        assert rid == ids[0] and result.status == "checked"
        assert [t["span"] for t in result.targets] == [list(t["span"]) for t in base.targets]
    finally:
        if ids:
            with psycopg.connect(dsn("postgres"), autocommit=True) as admin:
                admin.execute("delete from ops.eval_result where eval_run_id = any(%s)", (ids,))
                admin.execute("delete from ops.eval_run where id = any(%s)", (ids,))


@pytest.mark.parametrize("what", ["no_run", "not_first_pass", "two_hold_it", "not_checked", "rows_missing"])
def test_a_first_pass_that_cannot_stand_for_the_call_stops_the_trial(what):
    call = three_part_call()
    run, rows, _ = stored_first_pass(call)
    runs = [run]
    if what == "no_run":
        runs = []
    elif what == "not_first_pass":
        runs = [stored_first_pass(call, eval_set=trial.EVAL_SET_SECOND)[0]]
    elif what == "two_hold_it":
        other, more, _ = stored_first_pass(call, run_id=10)
        runs, rows = [run, other], rows + more
    elif what == "not_checked":
        run[2]["calls"][0]["status"] = "stopped"
    elif what == "rows_missing":
        rows = []
    with pytest.raises(SystemExit):
        trial.read_base(BaseConn(runs, rows), [9, 10] if what == "two_hold_it" else [9], [work(call)])


class SecondPassClient:
    """A fake Batch API for a second pass: answers each request with the targets it is given."""

    def __init__(self, targets):
        self.targets, self.sent = targets, []

    def create_batch(self, items):
        self.sent = list(items)
        return SimpleNamespace(id="msgbatch_second", ended=True, counts={})

    def batch(self, batch_id):
        return SimpleNamespace(id=batch_id, ended=True, counts={})

    def batch_results(self, info, model_of=lambda cid: None):
        for cid, _ in self.sent:
            yield BatchResult(cid, "succeeded", reply({"targets": self.targets, "stated_figures": []}, out=300))


def run_second_pass(monkeypatch, call, argv, client=None):
    run, rows, _ = stored_first_pass(call)
    conn, lines = BaseConn([run], rows), []
    monkeypatch.setattr("ft.jobs.runner.connect", lambda dsn, **kw: nullcontext(conn))
    monkeypatch.setattr(trial, "read_works", lambda c, events: [work(call)])
    code = trial.main(["--events", "3743", "--second-pass-of", "9", *argv], env={"JOB_DATABASE_URL": "postgresql://x/y"},
                      out=lines.append, client=client, sleep=lambda s: None)
    return code, conn, lines


def test_a_second_pass_asks_for_what_the_list_lacks_and_records_the_merged_targets(monkeypatch):
    call = three_part_call()
    ebitda = T(EBITDA, metric="ebitda_margin", metric_text="EBITDA margin", value=21, growth_basis="level")
    client = SecondPassClient([T(GROWTH), ebitda])
    code, conn, lines = run_second_pass(monkeypatch, call, ["--arms", "haiku sonnet:v4:low:12000", "--send", "--wait-minutes", "1"], client)
    assert code == 0
    assert [cid for cid, _ in client.sent] == ["trial-a0-e3743", "trial-a1-e3743"]
    first, other = (req for _, req in client.sent)
    assert first.prompt.version == other.prompt.version == "v4" and first.model == HAIKU and other.model == SONNET
    assert GROWTH in first.instruction and EBITDA not in first.instruction            # the list holds what the first pass kept
    assert first.sources == other.sources and first.instruction == other.instruction  # the arms differ in the second pass only
    (_, one_run), (_, two_run) = conn.runs
    assert one_run[:5] == (trial.EVAL_SET_SECOND, trial.SET_VERSION, HAIKU, "v4", 3)
    m = one_run[5].obj
    assert m["arm"] == "haiku:v4:task:task" and m["cost_usd"] == "0.013300"           # 0.001 for the first pass, 0.0123 for the second
    sp = m["second_pass"]
    assert sp["base_runs"] == {"3743": 9} and sp["base_cost_usd"] == "0.001000" and (sp["added"], sp["added_management"]) == (1, 1)
    assert sp["events"]["3743"] == {"added": 1, "added_management": 1, "first_pass_kept": 1, "first_pass_management": 1,
                                    "second_pass_status": "checked"}
    assert two_run[5].obj["second_pass"]["base_runs"] == {"3743": 9}                  # both arms share the one first pass
    mine = [(key, p.obj) for run_id, key, p in conn.results if run_id == conn.runs[0][0]]
    assert [k for k, _ in mine] == ["3743:0", "3743:1", "3743:2"] and [p["pass"] for _, p in mine] == [1, 2, 2]
    assert [p["duplicate_of"] for _, p in mine] == [None, 0, None]
    shown = " ".join(lines) + json.dumps([p.obj for _, _, p in conn.results] + [r[1][5].obj for r in conn.runs])
    assert not any(w in shown for w in (GROWTH, EBITDA, CAPEX))                        # no transcript text is stored or printed
    assert "second_pass" in shown and '"eval_run":42' in " ".join(lines).replace(" ", "")


def test_a_second_pass_dry_run_prices_the_requests_from_the_recorded_first_pass_and_sends_nothing(monkeypatch):
    code, conn, lines = run_second_pass(monkeypatch, three_part_call(), ["--arms", "haiku sonnet:v4:medium:16000"])
    assert code == 0 and conn.runs == [] and conn.results == []
    price = json.loads(next(x for x in lines if "trial price" in x).split("::", 2)[2].replace("%0A", "\n"))
    assert price["requests"] == 2 and price["second_pass_of"] == {"3743": 9} and Decimal(price["worst_case_usd"]) > 0
    assert any("trial dry run" in x for x in lines)


@pytest.mark.parametrize("argv", [["--arms", "haiku:v4:medium:16000:s3"], ["--arms", "haiku", "--second-pass-of", "x"],
                                  ["--arms", "haiku", "--second-pass-of", "9 9"], ["--arms", "gpt:v4"]])
def test_a_bad_second_pass_command_line_stops_before_any_connection(argv):
    with pytest.raises(SystemExit) as e:
        trial.main(["--events", "1", "--second-pass-of", "9", *argv], env={"JOB_DATABASE_URL": "postgresql://nowhere/none"})
    assert e.value.code == 2


def test_the_workflow_passes_the_second_pass_ids_through_the_environment():
    from test_workflows import load, manual_inputs, resolve, secrets
    wf = load("extraction-trial")
    spec = wf["on"]["workflow_dispatch"]["inputs"]["second_pass_of"]
    assert spec["type"] == "string" and spec["default"] == ""                          # an ordinary dispatch is still a first pass
    run = next(st["run"] for st in wf["jobs"]["run"]["steps"] if st.get("name") == "Trial")
    assert '"$SECOND_PASS_OF"' in run and "${{" not in run
    _, steps = resolve("extraction-trial", "workflow_dispatch", {**manual_inputs("extraction-trial"), "second_pass_of": "9,45"}, secrets(True))
    assert next(e for e in steps if "JOB_DATABASE_URL" in e)["SECOND_PASS_OF"] == "9,45"
